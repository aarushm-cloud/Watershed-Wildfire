"""run.py -- the single entrypoint (python run.py --fire <name>). Wires the
seven stages (ingest -> hydrology -> delineate -> score -> outputs) in order
and writes to out/<fire>/. The only place stage order is hardcoded; a thin
script, not a module (no orchestrator -- DECISIONS A7). See ARCHITECTURE.md.
"""
