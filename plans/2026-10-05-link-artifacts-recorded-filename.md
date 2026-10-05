# Plan: link artifacts show the recorded filename, not "Google Doc"

Status: **shipped** (PR #506, deployed `ed226f408`; verified live — the
four "Google Doc" rows now show `01_黃震遐醫生_…docx` etc., the folder
row shows "Agreements").

## Problem

Bare `docs.google.com/document/d/<id>/…` URLs in a card's result/summary
become `u:` artifacts titled by `_url_meta` — the generic label
"Google Doc". The `convert-pdf-to-docx-and-upload` run left four of them
in the unattached list, indistinguishable. The real names were recorded
all along — the confirm-upload card's run metadata carries
`files: [{"stored_name": "01_…_版權授權及版稅協議 (1).docx", "id":
"<drive id>"}]` — the artifacts read just never looked them up.

## Change

`_uploaded_copies` (the existing run-metadata walk that maps
basename→URL so rotted workspace deliveries open the uploaded copy)
now also returns `url_names` — doc-URL-key → recorded basename:

- `{name|filename|title|stored_name|file_name, link|url|webViewLink|…}`
  nodes → keyed by `_doc_url_key(link)`.
- `{…name…, id|file_id|fileId|document_id|doc_id}` nodes whose id looks
  like a Drive file id (`[A-Za-z0-9_-]{25,}`) → keyed by the raw id.

`_doc_url_key(url)` normalises Google links to their `/d/<id>` or
`/folders/<id>` id, so a bare mention (`…/edit?usp=sharing`) matches the
recorded link (`…/view`) or a bare `id` field. Non-Google URLs key on
the URL itself.

Consumers:

- `u:` link artifacts: title = markdown `[label]` → recorded name →
  `_url_meta` label (unchanged fallbacks).
- `url`-kind deliveries without a label: same preference order.

Verified against the production run's real metadata shape
(`files[].stored_name` + `id`, folder `{id,name,link}` — the folder row
now titles "Agreements" instead of its id).

## Tests

- Bare mention + `new_files` name/link → recorded name (different URL
  path/query still matches via the /d/ id).
- Bare mention + `files[].{stored_name,id}` (prod shape) → recorded name.
- Markdown `[label](url)` still wins over the recorded name.
- Unnamed URL still falls back to "Google Doc".
