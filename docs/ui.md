# Local UI

`treedb-project-memory ui` starts a local web console for the current workspace:

```sh
treedb-project-memory ui --host 127.0.0.1 --port 8765
```

The command binds only a local HTTP server by default and does not require
external network access at startup. The first screen is the memory console:
workspace status, configured sources, doctor diagnostics, search, ask, citation
results, and retrieval trace details.

The UI uses the same application services as the CLI:

- status calls the workspace status service;
- search calls the retrieval service;
- ask calls the answer service and reports the same missing-generator error as
  `treedb-project-memory ask`;
- doctor calls the workspace doctor report.

## Options

- `--host`: local interface to bind. Defaults to `127.0.0.1`.
- `--port`: local port to bind. Defaults to `8765`; use `0` for an available
  port.
- `--open / --no-open`: optionally open the default browser after startup.
- `--check-service`: include TreeDB service health during status refreshes.

## Stack Choice

The UI intentionally uses Python standard-library HTTP serving plus static
HTML/CSS/JavaScript embedded in the package. This keeps the optional local
console dependency-free, avoids a Node build step, and fits the existing Python
package while remaining easy to test with fake backends.
