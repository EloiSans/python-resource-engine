# Documentation site

`resource-engine docs site` turns the functional documentation into a static
website for people who don't read the repository: product owners, testers,
support, the teams that consume the API or its events.

```bash
uv run resource-engine docs site --out site --title "Facturació"
python -m http.server --directory site     # or open site/index.html
```

It's built from the same model as `docs generate`, which is the code itself:
resource and state-machine docstrings, schemas, permissions and events.

| Page | Contents |
|---|---|
| `index.html` | Catalogue of resources with their description and operations. |
| `resources/<name>.html` | Description, business area and actors; operations and endpoints; filters, tenancy, versions, restricted fields; references and relations; state machines with their diagram, states, transitions and timers; documents, hooks and schemas. |
| `permissions.html` | The system roles and the matrix of actions each one grants (with an authorization config). |
| `events.html` | The published events per channel, with their data (from the AsyncAPI document, written next to it as `asyncapi.json`). |

Every page has the menu and a search box (press `/`) over the text of every
page. It works offline. Diagrams are drawn in the browser by Mermaid, loaded
from a CDN by default. `--mermaid-url` points at a self-hosted copy (an
intranet without internet access), and `--mermaid-url ""` leaves the
diagrams as text. Pages follow the light or dark mode of the system, adapt to
phones and print without the menu.

Documentation text is Markdown. Raw HTML in a docstring is shown as text,
never run.

## Publishing

The example application's site is published at
<https://eloisans.github.io/python-resource-engine/> by
`.github/workflows/docs-site.yml` on every push to `main`. That workflow is a
template for your own application: it validates the documentation, builds the
site and deploys it to GitHub Pages (Settings → Pages → Source: GitHub
Actions).

The site is plain files: serve it from GitHub Pages, an S3 bucket, an nginx
on the intranet, or attach it to a release. In CI, build it after the
documentation checks so it always matches the code:

```yaml
- run: uv run resource-engine docs validate --strict
- run: uv run resource-engine docs site --out site
- uses: actions/upload-pages-artifact@v3
  with: {path: site}
```

The pages are generated: change the docstrings, schemas or permissions, not
the HTML.
