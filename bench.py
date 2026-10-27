#!/usr/bin/env python3
"""
bench.py : mesures comparatives de 4 stratégies de protection des données.

    python bench.py FICHIER [--reps 5]

Pour chaque stratégie (4+2, 6+2, 8+4, 3 copies) et chaque répétition :
  - temps de découpage : lecture à froid + encodage + écriture synchronisée ;
  - temps de réparation : perte d'un morceau de données, puis lecture des
    morceaux nécessaires (k pour l'erasure coding, 1 copie pour la
    réplication) + reconstruction + écriture.

Sorties : logs/bench.csv (toutes les mesures) et logs/tableau.md (synthèse).
Les morceaux sont écrits dans bench_tmp/ (disque local, sans conteneurs) afin
de mesurer le coût du codage et des E/S indépendamment de l'orchestration.
"""
import argparse
import csv
import os
import shutil
import statistics
import time
from pathlib import Path

import codec

RACINE = Path(__file__).resolve().parent
TMP = RACINE / "bench_tmp"
LOGS = RACINE / "logs"

# (nom, k, m) ; k = None désigne la réplication simple (3 copies).
CONFIGS = [("4+2", 4, 2), ("6+2", 6, 2), ("8+4", 8, 4), ("3 copies", None, None)]


def vider_cache(chemin: Path) -> None:
    """Retire du cache de pages les données d'un fichier (lecture à froid)."""
    fd = os.open(chemin, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def ecrire(chemin: Path, contenu: bytes) -> None:
    """Écrit sur disque (fsync) puis évince du cache : le prochain accès sera à froid."""
    with open(chemin, "wb") as f:
        f.write(contenu)
        f.flush()
        os.fsync(f.fileno())
        os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def dossier_propre(nom: str) -> Path:
    d = TMP / nom.replace("+", "p").replace(" ", "_")
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def bench_ec(src: Path, nom: str, k: int, m: int) -> dict:
    """Mesure une configuration k+m d'erasure coding."""
    n = k + m
    dossier = dossier_propre(nom)

    # --- Découpage ---
    vider_cache(src)
    t0 = time.perf_counter()
    donnees = src.read_bytes()
    t1 = time.perf_counter()
    morceaux = codec.encode(donnees, k, m)
    t2 = time.perf_counter()
    for i, contenu in enumerate(morceaux):
        ecrire(dossier / f"morceau.{i}", contenu)
    t3 = time.perf_counter()

    # --- Réparation : on perd le morceau 0 (un morceau de données, pire cas) ---
    perdu = 0
    attendu = morceaux[perdu]
    (dossier / f"morceau.{perdu}").unlink()
    t4 = time.perf_counter()
    lus = [i for i in range(n) if i != perdu][:k]          # k morceaux suffisent
    survivants = {i: (dossier / f"morceau.{i}").read_bytes() for i in lus}
    t5 = time.perf_counter()
    refait = codec.reparer(survivants, k, m, [perdu])[perdu]
    t6 = time.perf_counter()
    ecrire(dossier / f"morceau.{perdu}", refait)
    t7 = time.perf_counter()

    assert refait == attendu, f"{nom} : morceau reconstruit différent de l'original"
    return {
        "config": nom, "place": n / k, "tolerance": m,
        "t_lecture": t1 - t0, "t_calcul": t2 - t1, "t_ecriture": t3 - t2,
        "t_decoupage": t3 - t0,
        "t_rep_lecture": t5 - t4, "t_rep_calcul": t6 - t5,
        "t_rep_ecriture": t7 - t6, "t_reparation": t7 - t4,
        "octets_lus": k * len(attendu), "octets_perdus": len(attendu),
    }


def bench_replication(src: Path, copies: int = 3) -> dict:
    """Mesure la réplication simple : N copies complètes du fichier."""
    dossier = dossier_propre("3 copies")

    vider_cache(src)
    t0 = time.perf_counter()
    donnees = src.read_bytes()
    t1 = time.perf_counter()
    for i in range(copies):
        ecrire(dossier / f"copie.{i}", donnees)
    t3 = time.perf_counter()

    # Réparation : perte de la copie 0, recopie depuis la copie 1.
    (dossier / "copie.0").unlink()
    t4 = time.perf_counter()
    copie = (dossier / "copie.1").read_bytes()
    t5 = time.perf_counter()
    ecrire(dossier / "copie.0", copie)
    t7 = time.perf_counter()

    return {
        "config": "3 copies", "place": float(copies), "tolerance": copies - 1,
        "t_lecture": t1 - t0, "t_calcul": 0.0, "t_ecriture": t3 - t1,
        "t_decoupage": t3 - t0,
        "t_rep_lecture": t5 - t4, "t_rep_calcul": 0.0,
        "t_rep_ecriture": t7 - t5, "t_reparation": t7 - t4,
        "octets_lus": len(copie), "octets_perdus": len(copie),
    }


def mediane_ecart(valeurs: list) -> str:
    return f"{statistics.median(valeurs):.3f} ± {statistics.stdev(valeurs):.3f}"


def synthese(lignes: list) -> str:
    """Tableau markdown : médiane ± écart-type sur les répétitions."""
    sortie = [
        "| Découpage | Place | Pannes tolérées | Temps découpage (s) | "
        "Temps réparation (s) | Lecture/réparation |",
        "|---|---|---|---|---|---|",
    ]
    for nom, _, _ in CONFIGS:
        L = [l for l in lignes if l["config"] == nom]
        sortie.append(
            f"| {nom} | {L[0]['place']:.2f}x | {L[0]['tolerance']} | "
            f"{mediane_ecart([l['t_decoupage'] for l in L])} | "
            f"{mediane_ecart([l['t_reparation'] for l in L])} | "
            f"{L[0]['octets_lus'] / L[0]['octets_perdus']:.0f}x |")
    return "\n".join(sortie)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("fichier")
    p.add_argument("--reps", type=int, default=5)
    args = p.parse_args()

    src = Path(args.fichier)
    LOGS.mkdir(exist_ok=True)
    TMP.mkdir(exist_ok=True)
    print(f"fichier : {src} ({src.stat().st_size / 1e6:.0f} Mo), {args.reps} répétitions")

    lignes = []
    for r in range(args.reps + 1):                 # r = 0 : échauffement, ignoré
        for nom, k, m in CONFIGS:
            res = bench_replication(src) if k is None else bench_ec(src, nom, k, m)
            if r > 0:
                res["rep"] = r
                lignes.append(res)
        print("échauffement terminé" if r == 0 else f"répétition {r}/{args.reps} terminée")

    with open(LOGS / "bench.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]))
        w.writeheader()
        for l in lignes:
            w.writerow({c: (round(v, 5) if isinstance(v, float) else v)
                        for c, v in l.items()})

    tableau = synthese(lignes)
    (LOGS / "tableau.md").write_text(tableau + "\n")
    print("\n" + tableau)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
