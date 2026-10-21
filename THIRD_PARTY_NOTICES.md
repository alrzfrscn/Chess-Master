# Third-Party Notices

Chess Master 0.1.0 includes or depends on the following third-party work. The
full license texts that are distributed with source files are kept beside those
files; paths are listed below. Versions reflect the Phase 1 lockfiles.

## Bundled frontend libraries

### Chessground 9.2.1

- Copyright: Chessground contributors; original author Thibault Duplessis.
- License: GNU General Public License v3.0 or later (GPL-3.0-or-later).
- Source: <https://github.com/lichess-org/chessground>
- Distributed files: `src/chess_master/web/assets/vendor/chessground/`
- Full license: `src/chess_master/web/assets/vendor/chessground/LICENSE`

### chess.js 1.4.0

- Copyright (c) 2025 Jeff Hlywa.
- License: BSD 2-Clause.
- Source: <https://github.com/jhlywa/chess.js>
- Distributed file: `src/chess_master/web/assets/vendor/chess.js/chess.js`
- Full license: `src/chess_master/web/assets/vendor/chess.js/LICENSE`

### cburnett chess pieces

- SVG pieces extracted from Chessground's cburnett board theme.
- Original piece artwork by Colin M.L. Burnett.
- Distributed with Chessground under GPL-3.0-or-later; the extracted SVGs are in
  `src/chess_master/web/assets/pieces/`.
- See the Chessground license above.

### Tailwind CSS 4.3.3 and @tailwindcss/cli 4.3.3

- Copyright (c) Tailwind Labs, Inc.
- License: MIT.
- Source: <https://github.com/tailwindlabs/tailwindcss>
- Use: development-time CSS generation. The generated utility CSS in
  `src/chess_master/web/assets/css/app.css` is distributed with Chess Master.
- Full MIT text: see the appendix below.

## Bundled fonts

### Fira Sans

- Copyright (c) 2012-2015 The Mozilla Foundation and Telefonica S.A.
- License: SIL Open Font License 1.1.
- Files: `src/chess_master/web/assets/fonts/FiraSans-*.woff2`
- Full license: `src/chess_master/web/assets/fonts/OFL-FiraSans.txt`

### Fira Code

- Copyright 2014-2020 The Fira Code Project Authors.
- License: SIL Open Font License 1.1.
- File: `src/chess_master/web/assets/fonts/FiraCode-Regular.woff2`
- Full license: `src/chess_master/web/assets/fonts/OFL-FiraCode.txt`

## Python runtime dependencies

### python-chess 1.999 (project requirement: >=1.11)

- Copyright: Niklas Fiekas and contributors.
- License: GPL-3.0-or-later.
- Source: <https://github.com/niklasf/python-chess>
- Use: authoritative chess rules, PGN parsing and serialization, FEN
  validation, move replay and status evaluation.

### PyWebView 6.2.1 (project requirement: >=6.0)

- Copyright: Roman Sirokov and contributors.
- License: BSD 3-Clause.
- Source: <https://github.com/r0x0r/pywebview>
- Use: native desktop window and Python-to-JavaScript bridge.

PyWebView's own dependencies (including bottle, pythonnet, proxy-tools and
typing-extensions on Windows) remain under their respective licenses. They are
installed from the package index and are not vendored in this repository.

## Development-only tools

- pytest 9.1.1, MIT, <https://github.com/pytest-dev/pytest>
- Tailwind CLI, MIT (see above)
- Node.js build tooling, including esbuild, remains under its own MIT license
  and is not redistributed in the application bundle.

## Project licensing note

The original Chess Master source is declared MIT in `pyproject.toml`. Chessground
and python-chess are GPL-3.0-or-later. A distributed application that combines
these components must comply with the GPL, including source and notice
requirements. MIT is compatible with GPLv3, but the combined distribution is
subject to GPLv3 obligations. This notice is informational and is not legal
advice.

## Appendix: Tailwind CSS MIT license

```text
MIT License

Copyright (c) Tailwind Labs, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
