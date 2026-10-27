#!/usr/bin/env python3
"""
dashboard.py : tableau de bord Streamlit du stockage à codage d'effacement.

    streamlit run dashboard.py

Affiche l'état des nœuds et des morceaux (lecture de index.json et contrôle
des empreintes), le journal des réparations du surveillant et les résultats
du banc d'essai. Rafraîchissement automatique configurable.
"""
import subprocess
import time

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from repartir import RACINE, charger_index, inspecter

LOGS = RACINE / "logs"
ICONES = {"OK": "🟢 OK", "NOEUD_ARRETE": "🔴 Nœud arrêté",
          "MANQUANT": "🟠 Manquant", "CORROMPU": "🟣 Corrompu"}
ORDRE_CONFIGS = ["4+2", "6+2", "8+4", "3 copies"]

st.set_page_config(page_title="P3 - Stockage auto-réparant", layout="wide")
st.title("Stockage auto-réparant : tableau de bord")

with st.sidebar:
    auto = st.checkbox("Actualisation automatique", value=True)
    delai = st.slider("Période (s)", 2, 30, 5)


def etat_noeuds() -> dict:
    """{nom: True si le conteneur tourne}, trié par numéro de nœud."""
    r = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}",
         "--filter", "name=^noeud"], capture_output=True, text=True)
    etat = {}
    for ligne in r.stdout.splitlines():
        nom, statut = ligne.split("\t")
        etat[nom] = statut == "running"
    return dict(sorted(etat.items(), key=lambda kv: int(kv[0][5:])))


# ------------------------------------------------------------------ Mesures
index = charger_index()
noeuds = etat_noeuds()
lignes, resume_fichiers, par_noeud = [], [], {n: [] for n in noeuds}

for fid, e in index.items():
    statuts, _ = inspecter(fid, e)
    sains = sum(1 for s in statuts.values() if s == "OK")
    k, m = e["k"], e["m"]
    if sains == k + m:
        etat = "🟢 Sain"
    elif sains >= k:
        etat = f"🟠 Dégradé (réparable, {sains - k} panne(s) encore tolérée(s))"
    else:
        etat = "🔴 Perdu (tolérance dépassée)"
    resume_fichiers.append({"Fichier": fid, "Schéma": f"{k}+{m}",
                            "Morceaux sains": f"{sains}/{k + m}", "État": etat})
    for i in sorted(statuts):
        noeud = e["morceaux"][str(i)]["noeud"]
        par_noeud.setdefault(noeud, []).append(f"{fid}#{i}")
        lignes.append({"Fichier": fid, "Morceau": i,
                       "Type": "donnée" if i < k else "parité",
                       "Nœud": noeud, "État": ICONES[statuts[i]]})

# ------------------------------------------------------------------ Synthèse
c1, c2, c3 = st.columns(3)
c1.metric("Nœuds actifs", f"{sum(noeuds.values())}/{len(noeuds)}")
c2.metric("Fichiers protégés", len(index))
c3.metric("Morceaux sains",
          f"{sum(1 for l in lignes if l['État'] == ICONES['OK'])}/{len(lignes)}")

st.subheader("Nœuds")
colonnes = st.columns(max(1, len(noeuds)))
for col, (nom, actif) in zip(colonnes, noeuds.items()):
    col.markdown(f"**{'🟢' if actif else '🔴'} {nom}**")
    col.caption(", ".join(par_noeud.get(nom, [])) or "libre (secours)")

st.subheader("Fichiers")
if resume_fichiers:
    st.dataframe(pd.DataFrame(resume_fichiers), hide_index=True,
                 use_container_width=True)
    st.subheader("Détail des morceaux")
    st.dataframe(pd.DataFrame(lignes), hide_index=True, use_container_width=True)
else:
    st.info("Index vide : déposer un fichier avec `python repartir.py put`.")

# ------------------------------------------------------------------ Journal
st.subheader("Journal des réparations")
csv_rep = LOGS / "reparations.csv"
if csv_rep.exists():
    rep = pd.read_csv(csv_rep)
    a, b = st.columns(2)
    a.metric("Réparations effectuées", len(rep))
    b.metric("Durée médiane (s)", f"{rep['duree_s'].median():.3f}")
    st.dataframe(rep.iloc[::-1].head(20), hide_index=True, use_container_width=True)
else:
    st.caption("Aucune réparation enregistrée.")

journal = LOGS / "surveillant.log"
if journal.exists():
    with st.expander("Dernières lignes du journal du surveillant"):
        st.code("\n".join(journal.read_text().splitlines()[-15:]))

# ------------------------------------------------------------------ Bench
st.subheader("Comparaison des découpages (banc d'essai)")
csv_bench, tableau = LOGS / "bench.csv", LOGS / "tableau.md"
if csv_bench.exists():
    if tableau.exists():
        st.markdown(tableau.read_text())
    med = (pd.read_csv(csv_bench).groupby("config").median(numeric_only=True)
           .reindex(ORDRE_CONFIGS))
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.5))
    med[["t_lecture", "t_calcul", "t_ecriture"]].plot.bar(
        stacked=True, ax=ax[0], rot=0, title="Découpage (s, médiane)")
    med[["t_rep_lecture", "t_rep_calcul", "t_rep_ecriture"]].plot.bar(
        stacked=True, ax=ax[1], rot=0, title="Réparation (s, médiane)")
    for a in ax:
        a.legend(["lecture", "calcul", "écriture"])
        a.set_xlabel("")
    fig.tight_layout()
    st.pyplot(fig)
    fig.savefig(LOGS / "bench.png", dpi=150)   # figure réutilisable dans le rapport
    plt.close(fig)
else:
    st.caption("Lancer `python bench.py` pour produire les mesures.")

if auto:
    time.sleep(delai)
    st.rerun()
