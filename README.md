# Knowledge

Knowledge is a small Python MCP service for storing and retrieving structured project knowledge. This export contains version 1.2.0 and its canonical tests.

## Install

Requires Python 3.11 or newer. Copy this repository to a location of your choice, then configure an MCP client to launch `python3` with `knowledge.py` as its argument. The service communicates over standard input and output and has no third-party runtime dependencies.

## Run tests

Run the portable Knowledge tests with `python3 -m unittest discover -s tests -v`. Owner-specific launcher tests are excluded. The optional family-index integration tests are skipped unless `FAMILY_INDEX_EXTRACTOR` points to the separately supplied integration source.
