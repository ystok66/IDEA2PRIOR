import {build} from "esbuild";
import {fileURLToPath} from "node:url";
const here=fileURLToPath(new URL(".",import.meta.url));
await build({
  entryPoints:[fileURLToPath(new URL("../../paperarena/web_assets/renderer.entry.js",import.meta.url))],
  nodePaths:[here+"node_modules"],bundle:true,minify:true,format:"iife",legalComments:"eof",
  outfile:fileURLToPath(new URL("../../paperarena/web_assets/renderer.js",import.meta.url))
});
