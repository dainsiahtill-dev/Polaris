# Pelican riding a bicycle — animated website

Build a complete, polished offline website showing a pelican riding a bicycle. Deliver a real runnable `index.html` and its source assets in this workspace. This is an animated webpage, not only a static SVG or a written description.

The pelican must be recognizable, the bicycle structurally coherent, and their riding relationship convincing at ordinary viewing size. Show continuous visible wheel and crank/pedal motion, with the pelican's feet/legs moving consistently with the pedals. Allow artistic stylization, perspective and occlusion; choose an intentional visual style. Decorative motion alone does not satisfy the riding animation.

Provide clearly labelled play/pause and speed controls that actually affect the animation, support keyboard operation, and keep the scene and controls usable on desktop and narrow mobile screens. Respect reduced-motion preferences without making the ordinary default view unintentionally static. Avoid distracting clipping, broken layouts and browser errors.

The page must work offline without CDN assets, remote fonts, raster-image substitution, remote libraries or a dependency-install requirement. HTML/CSS/JavaScript and SVG are permitted. It should open locally and be usable when served as ordinary static files. Do not place project files under `.polaris`.

Use the selected collaborative workflow. PM provides concise product planning and quality priorities; Chief Engineer provides practical design, key technical decisions and verification advice. Director preserves their useful advice as short readable planning/design notes before implementation, then implements, tests and repairs based on real results. Do not create contract metadata, obligation graphs, candidate hashes or handoff schemas. Additional consultations remain optional within the original scope and budget.

Deliver concise opening instructions and a browser smoke test at `tests/browser_smoke.py`. The test must load the actual delivered page in Chromium, exercise play/pause/resume and speed interactions, observe time-varying riding motion rather than only searching source text, and fail on relevant browser errors. Avoid sampling only one periodic phase. Print actual checks/results, exit nonzero on failures, avoid modifying source or writing to workspace during final verification, and close every browser/server process started. Use one command for temporary preparation, execution and cleanup.

## Available execution environment

The host provides `/usr/bin/python3` (Python 3.12) and preinstalled Playwright through explicitly read-only mounted dependencies. Run the smoke test with:

`PYTHONPATH=/home/dains/.pyenv/versions/3.12.3/lib/python3.12/site-packages /usr/bin/python3 -B tests/browser_smoke.py`

Use the installed headless executable `/home/dains/.cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell` explicitly when launching Chromium. A browser download or dependency installation is unnecessary. Open the offline page using its local `file://` URL. A server in another command will not remain running; do not depend on one. Private namespace loopback has not been promised.

Each command has a fresh private `/tmp`, HOME, cache and sandbox-output; these do not survive a separate tool call. Only permitted workspace files persist. Keep browser profile/temp state private to one command and close it before returning. External networking is disabled. Only the listed package/browser paths are readable; the rest of the host home and packages are outside the grant. Do not access `.polaris` control data.

Independent native QA must review current source and actual verification observations, using its own read-only checks when useful. Be precise about observation limits: current tools return text; saving a screenshot or printing its hash does not supply an image to the model. Source comments or claimed visual quality are not evidence of what the animation looks like.

No fixed element count, file count, illustration coordinates, animation technique, framework or artistic reference is required. Finish when the user-visible behavior works and has real current-candidate verification. Long-term memory is out of scope.
