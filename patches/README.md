# Patches

## `fb-mcp-openeye.patch`

OpenEye's pipeline relies on additions to the **Facebook Marketplace MCP**
([fisheyes/mcp-facebook-market-place](https://github.com/fisheyes/mcp-facebook-market-place))
that aren't in upstream. Because that MCP is a separate third-party repo you clone yourself,
those changes can't ship inside this repo directly — so they're captured here as a patch.

**What it adds to `scraper.py`:**
- `--max-price` (Facebook `maxPrice` filter; `0` = **free-only sweep**)
- `--details <listing_id>` (fetch a listing's description/condition as JSON — powers defect &
  intent reading)
- `--scroll N` (paced, jittered scrolling for deeper results)
- `--radius-km` / `--lat` / `--lng` (best-effort geographic widening)
- `--login` + `FB_PROFILE_DIR` (persistent logged-in session via Playwright `launch_persistent_context`)

**Base commit:** upstream `81a9f46` ("Add troubleshooting note about first-time Facebook login…").

### How to apply

```powershell
# 1. Clone the upstream MCP (if you haven't):
git clone https://github.com/fisheyes/mcp-facebook-market-place
cd mcp-facebook-market-place

# 2. (Recommended) check out the base commit the patch was made against:
git checkout 81a9f46

# 3. Apply the patch:
git apply C:\path\to\OpenEye\patches\fb-mcp-openeye.patch

# 4. Point OpenEye at it: set FB_MCP_DIR in OpenEye/.env to this folder.
```

If `git apply` reports conflicts (because upstream moved on), apply with fuzz or review by hand:

```powershell
git apply --3way C:\path\to\OpenEye\patches\fb-mcp-openeye.patch
# or, to see what it would change without applying:
git apply --stat   C:\path\to\OpenEye\patches\fb-mcp-openeye.patch
git apply --check  C:\path\to\OpenEye\patches\fb-mcp-openeye.patch
```

All OpenEye additions in the patch are clearly commented with `OpenEye:` so they're easy to
locate or port forward if upstream changes.
