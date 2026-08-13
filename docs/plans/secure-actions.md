# Secure external actions

External action should be pinned to a specific, known safe hash.

## Status: implemented

All external action references in `.github/workflows/*.yml` are pinned to a
specific commit SHA with a trailing `# <tag>` comment for traceability:

| Action | Tag | Pinned commit SHA |
| --- | --- | --- |
| `actions/checkout` | v6 | `d23441a48e516b6c34aea4fa41551a30e30af803` |
| `actions/setup-python` | v6 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |
| `actions/upload-artifact` | v4 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `astral-sh/setup-uv` | v6 | `d0d8abe699bfb85fec6de9f7adb5ae17292296ff` |

The `docs/examples/*.yml` reusable-workflow call sites use a `<pinned-sha>`
placeholder because the consuming repository must pin to a SHA of *this*
repository's workflow; that is a consumer responsibility, not something this
repository can pin for them.

### Updating a pin

To rotate a pin to a newer tag release, resolve the tag to its commit SHA and
replace the SHA (keeping the `# <tag>` comment in sync):

```text
curl -fsSL "https://api.github.com/repos/<owner>/<repo>/git/refs/tags/<tag>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['object']['sha'])"
```
