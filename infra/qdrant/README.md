# Qdrant

Collections are created and versioned by `services/risk-knowledge` (`python -m app.cli.index_knowledge`).
Naming: `${KNOWLEDGE_COLLECTION_PREFIX}_v<collection_version>` with alias `${KNOWLEDGE_COLLECTION_PREFIX}_active`.
Rollback = repoint the alias to the previous versioned collection (`python -m app.cli.index_knowledge --rollback <version>`).
Data lives in the `qdrant-data` volume; never `docker compose down -v` without a backup.
