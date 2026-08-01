# schema-monitor

A JSON-LD Product schema validator you can run on your laptop. Built to learn on.

It does one thing most free schema checkers do not: it compares the raw HTML
against the rendered DOM, so it tells you whether your structured data is
server-side or injected by JavaScript.

## Setup

```bash
# 1. get into the folder
cd schema-monitor

# 2. make a virtual environment so you do not pollute system python
python3 -m venv .venv

# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 3. install
pip install -r requirements.txt

# 4. only needed for --render
playwright install chromium
```

## Run it

The test fixtures need to be served over HTTP so the browser can load them.
Open a second terminal, leave this running:

```bash
python3 -m http.server 8000
```

Back in the first terminal:

```bash
# fast check, raw HTML only
python3 schema_check.py urls.txt

# full check, also loads each page in a headless browser
python3 schema_check.py urls.txt --render
```

## What the fixtures demonstrate

| file | what is wrong with it |
|---|---|
| `good.html` | nothing. this is the baseline |
| `missing_fields.html` | no image, relative `@id`, brand as a string, offer missing currency and availability |
| `client_side.html` | schema is valid, but only exists after JavaScript runs |
| `no_schema.html` | no structured data at all |
| `graph_wrapped.html` | valid, but wrapped in `@graph` alongside Organization and BreadcrumbList |

Run without `--render` first, then with it. Watch `client_side.html` flip from
"no Product JSON-LD found" to "ok, rendering: client-side only". That flip is
the whole point of the tool.

## What is in the script

- `validate_product()` holds the rules. Everything else is plumbing.
  Edit the three field lists at the top to change what counts as correct.
- `fetch_raw()` vs `fetch_rendered()` is the raw-versus-rendered comparison.
- `load_previous()` and the diff logic mean you only get alerted on things
  that newly broke, not on the same 40 warnings every run.
- Exit code is 1 when something newly fails. That is what a scheduler reads.

## Things to try, roughly in order of difficulty

1. Add a rule: fail if `offers.price` is not a number, or is zero.
2. Add a rule: warn if `availability` is not one of the valid schema.org values.
3. Extend it to validate `Organization` and `BreadcrumbList`, not just Product.
4. Read URLs from a sitemap.xml instead of a text file, and sample 50 at random.
5. Write the report as HTML instead of JSON so it is readable in a browser.
6. Point it at a real public catalog. Read their robots.txt first, keep the
   delay, and use a real contact address in USER_AGENT.
7. Only once all of the above works: add a GitHub Actions cron schedule.

## Etiquette if you point it at a site you do not own

- Check `/robots.txt` and respect it.
- Keep `DELAY_SECONDS` at 1 or higher.
- Sample. Do not crawl every URL.
- Put a real contact address in `USER_AGENT`.
- Do not publish findings about a specific named company you have a
  relationship with.
