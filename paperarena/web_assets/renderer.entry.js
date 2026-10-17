import {marked} from "marked";
import DOMPurify from "dompurify";
import mermaid from "mermaid";
mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"neutral",maxTextSize:100000,htmlLabels:false,flowchart:{htmlLabels:false}});
let sequence=0;
window.ResearchRenderer={
  async render(element,source){
    const token=String(++sequence);element.dataset.renderToken=token;
    element.innerHTML=DOMPurify.sanitize(marked.parse(source||""),{FORBID_TAGS:["img","style","iframe","object","input","form"],FORBID_ATTR:["style"]});
    for(const link of element.querySelectorAll("a")){link.target="_blank";link.rel="noopener noreferrer";}
    for(const code of element.querySelectorAll("code.language-mermaid")){
      try{
        const {svg}=await mermaid.render("research-graph-"+(++sequence),code.textContent);
        if(element.dataset.renderToken!==token)return;
        const graph=document.createElement("div");graph.className="mermaid-diagram";
        // Some diagram types still emit HTML labels. Convert those to SVG text,
        // then remove all HTML/style/event capabilities from the final graph.
        const parsed=new DOMParser().parseFromString(svg,"image/svg+xml");
        for(const label of parsed.querySelectorAll("foreignObject")){
          const text=parsed.createElementNS("http://www.w3.org/2000/svg","text");
          text.textContent=label.textContent;text.setAttribute("x",String(Number(label.getAttribute("width")||0)/2));
          text.setAttribute("y",String(Number(label.getAttribute("height")||0)/2));
          text.setAttribute("text-anchor","middle");text.setAttribute("dominant-baseline","central");label.replaceWith(text);
        }
        graph.innerHTML=DOMPurify.sanitize(new XMLSerializer().serializeToString(parsed),{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:["style","foreignObject"],FORBID_ATTR:["style"]});
        code.parentElement.replaceWith(graph);
      }catch(_){/* Keep escaped source visible when diagram syntax is invalid. */}
    }
  }
};
