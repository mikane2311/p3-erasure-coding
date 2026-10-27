#!/usr/bin/env python3
"""
repartir.py : répartition des morceaux d'erasure coding sur les nœuds Docker.

    python repartir.py put FICHIER K M [--noeuds N]
    python repartir.py get ID SORTIE
    python repartir.py etat

Règle d'or : deux morceaux d'un même fichier ne sont jamais placés sur le même
nœud, de sorte qu'une panne de nœud ne coûte qu'un seul morceau par fichier.
Les métadonnées (nœud de chaque morceau, empreintes) sont stockées dans
index.json, en dehors des nœuds : elles survivent aux pannes de stockage.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import codec

RACINE = Path(__file__).resolve().parent
SRV = RACINE / "srv"          # srv/noeudN/ est le volume monté dans le conteneur noeudN
INDEX = RACINE / "index.json"


# --------------------------------------------------------------------------
# Index (métadonnées)
# --------------------------------------------------------------------------

def charger_index() -> dict:
    """Charge l'index ; retourne un index vide s'il n'existe pas encore."""
    if INDEX.exists():
        return json.loads(INDEX.read_text())
    return {}


def sauver_index(index: dict) -> None:
    """Écriture atomique : un crash en cours d'écriture ne corrompt pas l'index."""
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    os.replace(tmp, INDEX)


# --------------------------------------------------------------------------
# Accès aux nœuds
# --------------------------------------------------------------------------

def noeud_actif(nom: str) -> bool:
    """Un nœud est disponible si son conteneur tourne (docker inspect)."""
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", nom],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def inspecter(fid: str, entree: dict):
    """
    Contrôle l'état de chaque morceau d'un fichier.

    Retourne (statuts, disponibles) :
      statuts      : {indice: OK | NOEUD_ARRETE | MANQUANT | CORROMPU}
      disponibles  : {indice: contenu} des seuls morceaux valides
    """
    statuts, disponibles = {}, {}
    for cle, info in entree["morceaux"].items():
        i, noeud = int(cle), info["noeud"]
        if not noeud_actif(noeud):
            statuts[i] = "NOEUD_ARRETE"
            continue
        chemin = SRV / noeud / fid / f"morceau.{i}"
        if not chemin.exists():
            statuts[i] = "MANQUANT"
            continue
        contenu = chemin.read_bytes()
        if codec.md5(contenu) != info["hash"]:
            statuts[i] = "CORROMPU"
            continue
        statuts[i], disponibles[i] = "OK", contenu
    return statuts, disponibles


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------

def cmd_put(fichier: str, k: int, m: int, nb_noeuds: int) -> None:
    n = k + m
    actifs = [f"noeud{i}" for i in range(1, nb_noeuds + 1)
              if noeud_actif(f"noeud{i}")]
    if len(actifs) < n:
        sys.exit(f"ERREUR : {n} nœuds actifs requis pour {k}+{m}, "
                 f"{len(actifs)} disponibles (relancer ./setup.sh {n}).")

    chemin = Path(fichier)
    fid = chemin.name
    donnees = chemin.read_bytes()

    t0 = time.perf_counter()
    morceaux = codec.encode(donnees, k, m)
    t_encode = time.perf_counter() - t0

    # Un fichier redéposé remplace l'ancien : purge des anciens morceaux.
    for ancien in SRV.glob(f"noeud*/{fid}"):
        shutil.rmtree(ancien)

    index = charger_index()
    # Rotation du point de départ : équilibre la charge entre fichiers successifs.
    d = len(index) % len(actifs)
    ordre = actifs[d:] + actifs[:d]

    entree = {"nom": fid, "k": k, "m": m, "taille": len(donnees),
              "md5": codec.md5(donnees), "cree": time.strftime("%F %T"),
              "morceaux": {}}
    for i, contenu in enumerate(morceaux):
        noeud = ordre[i]                       # i < n <= len(ordre) : nœuds tous distincts
        dossier = SRV / noeud / fid
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / f"morceau.{i}").write_bytes(contenu)
        entree["morceaux"][str(i)] = {"noeud": noeud, "hash": codec.md5(contenu)}

    # Vérification de l'invariant de placement avant de valider l'index.
    placements = [v["noeud"] for v in entree["morceaux"].values()]
    assert len(set(placements)) == len(placements), "2 morceaux sur le même nœud"

    index[fid] = entree
    sauver_index(index)
    print(f"{fid} : {k}+{m} réparti sur {sorted(set(placements))} "
          f"(encodage {t_encode * 1000:.1f} ms)")


def cmd_get(fid: str, sortie: str) -> None:
    index = charger_index()
    if fid not in index:
        sys.exit(f"ERREUR : fichier '{fid}' absent de l'index.")
    e = index[fid]
    statuts, dispo = inspecter(fid, e)
    print("statuts :", {i: statuts[i] for i in sorted(statuts)})
    try:
        donnees = codec.decode(dispo, e["k"], e["m"], e["taille"])
    except codec.IrrecuperableError as err:
        sys.exit(f"ERREUR : {err}")
    Path(sortie).write_bytes(donnees)
    verdict = "OK" if codec.md5(donnees) == e["md5"] else "DIFFÉRENT"
    print(f"md5 {codec.md5(donnees)} | {verdict}")


def cmd_etat() -> None:
    index = charger_index()
    if not index:
        print("index vide")
    for fid, e in index.items():
        statuts, _ = inspecter(fid, e)
        print(f"\n{fid}  ({e['k']}+{e['m']}, {e['taille']} octets)")
        for i in sorted(statuts):
            print(f"  morceau {i:>2}  {e['morceaux'][str(i)]['noeud']:<8} {statuts[i]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("put")
    a.add_argument("fichier"); a.add_argument("k", type=int); a.add_argument("m", type=int)
    a.add_argument("--noeuds", type=int, default=6)
    b = sub.add_parser("get")
    b.add_argument("id"); b.add_argument("sortie")
    sub.add_parser("etat")
    args = p.parse_args()
    if args.cmd == "put":
        cmd_put(args.fichier, args.k, args.m, args.noeuds)
    elif args.cmd == "get":
        cmd_get(args.id, args.sortie)
    else:
        cmd_etat()
