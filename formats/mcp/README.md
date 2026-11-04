# MCP

`python -m opsdesk.fullcare.mcp --server http://127.0.0.1:8080` serves every workspace tool over MCP (stdio). Calls go to
the same `/api/step` as the screens, so GUI and MCP agents change the same state. Image: `opsdesk-fullcare-sample:fullcare-0.6.4`.
