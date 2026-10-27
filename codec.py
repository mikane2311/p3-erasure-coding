#!/usr/bin/env python3
"""
codec.py : codage à effacement (erasure coding) de type Reed-Solomon.

Un fichier est découpé en k morceaux de données, auxquels s'ajoutent
m morceaux de parité. N'importe quels k morceaux parmi les n = k + m
suffisent à reconstruire le fichier : le système tolère donc m pannes
pour un surcoût de stockage de (k + m) / k.

Utilisation en ligne de commande :
    python codec.py encode FICHIER K M DOSSIER
    python codec.py decode DOSSIER SORTIE
"""
import sys
import os
import json
import hashlib
import time
import glob

import zfec  # bibliothèque C de Reed-Solomon (rapide, adaptée aux mesures)


class IrrecuperableError(Exception):
    """Levée quand il reste moins de k morceaux : reconstruction impossible."""


def md5(donnees: bytes) -> str:
    """Empreinte md5 : sert de preuve d'intégrité et de détection de corruption."""
    return hashlib.md5(donnees).hexdigest()


# --------------------------------------------------------------------------
# Fonctions de bibliothèque (réutilisées par repartir.py et surveillant.py)
# --------------------------------------------------------------------------

def encode(donnees: bytes, k: int, m: int) -> list:
    """
    Découpe `donnees` en k blocs de taille égale et calcule m blocs de parité.

    Retourne une liste de n = k + m morceaux :
      - indices 0..k-1   : blocs de données (code systématique)
      - indices k..n-1   : blocs de parité
    """
    # Taille d'un bloc = division entière par excès (au moins 1 octet).
    taille_bloc = max(1, -(-len(donnees) // k))

    # Bourrage par des zéros pour obtenir exactement k blocs de même taille.
    # La taille d'origine est conservée dans les métadonnées pour le retirer.
    complete = donnees.ljust(taille_bloc * k, b"\0")
    blocs = [complete[i * taille_bloc:(i + 1) * taille_bloc] for i in range(k)]

    # L'encodeur renvoie les n morceaux (les k premiers sont les blocs d'entrée).
    return [bytes(morceau) for morceau in zfec.Encoder(k, k + m).encode(blocs)]


def _blocs_donnees(morceaux: dict, k: int, m: int):
    """
    Reconstitue les k blocs de données à partir de k morceaux quelconques.

    `morceaux` : dict {indice: contenu} des morceaux encore disponibles.
    """
    if len(morceaux) < k:
        raise IrrecuperableError(
            f"Reconstruction impossible : {len(morceaux)} morceaux disponibles, "
            f"{k} requis (tolérance = {m} pannes)."
        )
    # Seuls k morceaux sont nécessaires : on prend les plus petits indices,
    # ce qui privilégie les blocs de données (décodage plus rapide).
    indices = sorted(morceaux)[:k]
    return zfec.Decoder(k, k + m).decode([morceaux[i] for i in indices], indices)


def decode(morceaux: dict, k: int, m: int, taille: int) -> bytes:
    """Reconstruit le fichier d'origine et supprime le bourrage final."""
    blocs = _blocs_donnees(morceaux, k, m)
    return b"".join(bytes(b) for b in blocs)[:taille]


def reparer(morceaux: dict, k: int, m: int, manquants: list) -> dict:
    """
    Recalcule uniquement les morceaux listés dans `manquants`.

    Coût : la lecture de k morceaux survivants, quel que soit le nombre de
    morceaux à refabriquer. C'est pourquoi la réparation est plus lente
    pour un grand k (8+4) que pour un petit k (4+2).

    Retourne un dict {indice: contenu}.
    """
    blocs = _blocs_donnees(morceaux, k, m)
    refaits = zfec.Encoder(k, k + m).encode(blocs, list(manquants))
    return {i: bytes(r) for i, r in zip(manquants, refaits)}


# --------------------------------------------------------------------------
# Interface en ligne de commande (démonstration et tests unitaires manuels)
# --------------------------------------------------------------------------

def cli_encode(fichier: str, k: int, m: int, dossier: str) -> None:
    """Encode `fichier` et écrit les morceaux + meta.json dans `dossier`."""
    os.makedirs(dossier, exist_ok=True)
    with open(fichier, "rb") as f:
        donnees = f.read()

    debut = time.perf_counter()
    morceaux = encode(donnees, k, m)
    duree = time.perf_counter() - debut

    for i, contenu in enumerate(morceaux):
        with open(f"{dossier}/morceau.{i}", "wb") as f:
            f.write(contenu)

    # Métadonnées nécessaires au décodage et à la vérification d'intégrité.
    meta = {
        "k": k, "m": m,
        "taille": len(donnees),                  # pour retirer le bourrage
        "md5": md5(donnees),                     # empreinte du fichier d'origine
        "hash": [md5(c) for c in morceaux],      # empreinte de chaque morceau
    }
    with open(f"{dossier}/meta.json", "w") as f:
        json.dump(meta, f)

    print(f"{k}+{m} : {len(morceaux)} morceaux, "
          f"découpage {duree * 1000:.1f} ms, md5 {meta['md5']}")


def cli_decode(dossier: str, sortie: str) -> None:
    """Reconstruit le fichier à partir des morceaux valides de `dossier`."""
    with open(f"{dossier}/meta.json") as f:
        meta = json.load(f)

    # Chargement des morceaux présents ; ceux dont l'empreinte ne correspond
    # pas (corrompus) sont traités comme absents.
    morceaux = {}
    for chemin in glob.glob(f"{dossier}/morceau.*"):
        indice = int(chemin.rsplit(".", 1)[1])
        with open(chemin, "rb") as f:
            contenu = f.read()
        if md5(contenu) == meta["hash"][indice]:
            morceaux[indice] = contenu
    print(f"morceaux valides : {sorted(morceaux)}")

    try:
        resultat = decode(morceaux, meta["k"], meta["m"], meta["taille"])
    except IrrecuperableError as erreur:
        # Cas > m pannes : message explicite, code de sortie non nul, pas de crash.
        print("ERREUR :", erreur)
        sys.exit(2)

    with open(sortie, "wb") as f:
        f.write(resultat)
    verdict = "OK" if md5(resultat) == meta["md5"] else "DIFFÉRENT"
    print("md5 reconstruit :", md5(resultat), "|", verdict)


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "encode":
        cli_encode(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    elif len(sys.argv) == 4 and sys.argv[1] == "decode":
        cli_decode(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
