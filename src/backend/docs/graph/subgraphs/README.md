# Graph Subgraphs

This directory stores executable subgraph truth assets.

Current truth-aligned subgraphs referenced by `docs/graph/catalog/cells.yaml`:

- `storage_archive_pipeline.yaml`

Additional `.yaml` files may exist here as migration drafts. A draft subgraph does
not become current graph truth until:

1. Its referenced Cells are declared in `docs/graph/catalog/cells.yaml`
2. The supporting `polaris/cells/**` assets exist
3. The file is treated as current fact in the architecture documents and tests

When adding a new subgraph, ensure the referenced Cells already exist in
`docs/graph/catalog/cells.yaml` and validate the file against
`docs/governance/schemas/subgraph.schema.yaml`.
