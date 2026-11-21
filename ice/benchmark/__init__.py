"""benchmark — ICE benchmark harness (spec Part 4 §17 + audit gaps).

Owner:      ICE revamp, Part 4 §17 (benchmark harness), audit gaps #12/#13/
            #14/#15/#16/#36/#37.
Purpose:    Offline evaluation of the Investigation Continuity Engine:
            * runner.py    — materialize benchmark case directories as real
                             cases and drive the full engine over them
                             (§17.2, + gap fixes #36/#37).
            * metrics.py   — hypothesis ranking accuracy, contradiction
                             precision, evidence trace coverage, mean
                             latency, decision accuracy (§17.3), plus the
                             audit-mandated investigation efficiency (#13)
                             and evidence utilization (#14).
            * baselines.py — comparison targets B1–B6 (§17.4).
            * ablation.py  — ablation runner/matrix (audit gap #15).
            * cases/       — five benchmark case directories (§17.1 format;
                             archetypes per audit gap #16).
Deps:       case_engine public API only (Part 3 §0 rule 12) + engine.memory
            substrate for the vector-RAG baseline.
"""