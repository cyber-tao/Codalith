# UE Context Engine

UE Context Engine is a Python MCP gateway that wraps CodeRAG-style retrieval with
Unreal Engine-aware corpus resolution, source-read policy, audit logging, semantic
extractors, knowledge-card verification, and evaluation tooling.

## Development

Run all default checks in Docker Compose:

```bash
docker compose run --rm test
```

Run the optional UE source smoke path with UE 5.7 mounted into the container:

```bash
docker compose --profile ue run --rm ue-acceptance
```

The compose file expects the local `.env` file to contain any AI API settings required by
CodeRAG. The repository never commits `.env`.
