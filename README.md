# Knowledge

Knowledge is a Python MCP service for storing and retrieving structured project knowledge. This export contains Knowledge version `1.2.0`, matching the service shipped in TEAA `0.5.0-alpha.5`.

Knowledge `1.2.0` is included in the TEAA alpha release. The combined TEAA distribution is alpha; this Knowledge source export identifies as version `1.2.0`.

## Install the free launch release

The release assets are published on the [TEAA GitHub releases](https://github.com/ToolsEnabled/teaa/releases):

- Linux x64: `teaa-0.5.0-alpha.5-linux-x64.tar.gz`
- Native Windows x64: `teaa-0.5.0-alpha.5-windows-x64.zip`
- Checksums: `SHA256SUMS`

### Linux

Download the Linux archive and `SHA256SUMS` into one directory, then run:

```sh
sha256sum -c --ignore-missing SHA256SUMS
tar -xzf teaa-0.5.0-alpha.5-linux-x64.tar.gz
cd teaa-0.5.0-alpha.5-linux-x64
./install.sh
```

### Native Windows

Download the Windows zip and `SHA256SUMS`. In PowerShell, run `Get-FileHash .\teaa-0.5.0-alpha.5-windows-x64.zip -Algorithm SHA256` and compare the result with the Windows zip line in `SHA256SUMS`. Extract the zip, open Command Prompt in the extracted `teaa-0.5.0-alpha.5-windows-x64` folder, and run:

```bat
install.cmd
```

Node.js 22.19 or newer and Python 3.11 or newer are required on both systems. Git is required for Knowledge repository operations. After installation, create or choose an absolute project folder and run:

```text
teaa setup --folder <absolute-project-folder> --actor local --no-browser --workers
teaa doctor
teaa config --server-json
```

Register the JSON from `teaa config --server-json` as a stdio MCP server in your client. See [TEAA SETUP.md](https://github.com/ToolsEnabled/teaa/blob/main/SETUP.md) for first-run details.

## Run Knowledge from source

Requires Python 3.11 or newer. Launch `python3 knowledge.py` as a stdio MCP server. Knowledge uses the Python standard library and has no third-party runtime dependencies.

Run the portable tests with `python3 -m unittest discover -s tests -v`. Owner-specific launcher tests are excluded. Optional family-index integration tests are skipped unless `FAMILY_INDEX_EXTRACTOR` points to the separately supplied integration source.
