# Examples

These example workspaces use generic local content and relative roots. They are
intended for docs, tests, and quick inspection. Relative roots are resolved from
the process current working directory, so run commands from the example
workspace root.

Run one from its example directory:

```sh
cd examples/simple-repo
treedb-project-memory doctor --format json
treedb-project-memory index --dry-run
```

The configs use `treedb.adapter: memory` so they do not require optional
TreeDB/Haystack packages for dry-run and indexing smoke checks. For persistent
search across separate CLI commands, switch to the Haystack adapter and run an
external TreeDB document service.

Available examples:

- [simple repo](simple-repo/README.md)
- [docs folder](docs-folder/README.md)
- [multi-source workspace](multi-source-workspace/README.md)
