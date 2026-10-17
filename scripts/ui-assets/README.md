# Local Markdown and Mermaid renderer

The browser uses the committed renderer.js bundle; no CDN requests are needed.
Source: paperarena/web_assets/renderer.entry.js. Versions are locked here.

From the repository root:

    pnpm --dir scripts/ui-assets install --frozen-lockfile
    node scripts/ui-assets/build.mjs

On filesystems without symlinks, the supplied .npmrc selects a hoisted layout.
Runtime licenses are shipped in paperarena/web_assets/THIRD_PARTY_LICENSES.txt.
Model HTML is sanitized with DOMPurify; Mermaid uses strict security mode.
