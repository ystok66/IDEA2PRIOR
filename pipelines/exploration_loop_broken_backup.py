    agent,
    state: PipelineState,
    max_rounds: int = 2,
    max_papers: int = 80,
    min_new_per_round: int = 5,
) -> PipelineState:
    """
    Run the multi-round exploration loop.

    Parameters
    ----------
    agent:
        The CodeAgent instance, already configured with OpenAlex / S2 tools.
    state:
        PipelineState containing the parsed Intent. This object will be
        updated in-place and also returned.
    max_rounds:
        Maximum number of exploration rounds.
    max_papers:
        Global upper bound on the number of papers in the state.
    min_new_per_round:
        If a round discovers fewer than this many new papers, the loop stops early.

    Returns
    -------
    PipelineState
        The final state after exploration completes.
    """
    logger.info(
        f"Starting exploration loop for topic='{state.intent.topic}', "
        f"task_type='{state.intent.task_type}'"
    )

    for round_idx in range(max_rounds):
        logger.info(f"=== Exploration round {round_idx + 1}/{max_rounds} ===")
        state.discovery_round = round_idx + 1

        # 1) Discover new papers for this round.
        newly_added = discover_papers_round(agent, state)

        logger.info(f"Round {round_idx + 1}: discovered {len(newly_added)} new papers.")
        logger.info(f"Total papers in state: {len(state.papers)}")

        # Early stop if exploration is no longer adding useful papers.
        if len(newly_added) < min_new_per_round:
            logger.info(
                f"Stopping exploration: only {len(newly_added)} new papers "
                f"(min_new_per_round={min_new_per_round})."
            )
            break

        if len(state.papers) >= max_papers:
            logger.info(
                f"Stopping exploration: reached max_papers={max_papers} "
                f"(current={len(state.papers)})."
            )
            break

        # 2) Enrich metadata and citation graph for a subset of key papers.
        enrich_metadata_and_citations(agent, state)

        # 3) Optionally parse a small number of key PDFs for deeper signals.
        maybe_parse_key_papers(agent, state, round_idx=round_idx)

    logger.info(
        f"Exploration loop finished with {len(state.papers)} papers "
        f"after {state.discovery_round} rounds."
    )
    return state


def discover_papers_round(agent, state: PipelineState) -> List[Paper]:
    """
    Discover a batch of new papers for the current round.
    """
    logger.info("Discovering papers via OpenAlex...")
    
    # 1. Construct query from Intent
    topic = state.intent.topic
    years = state.intent.years
    venues = state.intent.venues
    
    from_year = years[0] if years else None
    to_year = years[1] if years else None
    # OpenAlex uses | for OR in filters
    venue_str = "|".join(venues) if venues else None
    
    # 2. Call OpenAlex tool
    oa_tool = OpenAlexWorksSearchTool()
    
    # The tool returns a JSON string (list of dicts)
    try:
        result_json = oa_tool.forward(
            query=topic,
            from_year=from_year,
            to_year=to_year,
            venue=venue_str,
            limit=20
        )
        
        if result_json.startswith("Error"):
            logger.error(f"OpenAlex tool returned error: {result_json}")
            return []
            
        works = json.loads(result_json)
    except Exception as e:
        logger.error(f"Failed to query or parse OpenAlex results: {e}")
        return []

    newly_added = []
    
    # 3. Parse and deduplicate
    for work in works:
        # Extract fields
        title = work.get("title", "Unknown Title")
        year = work.get("year")
        venue = work.get("venue")
        openalex_id = work.get("openalex_id")
        doi = work.get("doi")
        # Clean DOI if it's a URL
        if doi and "doi.org/" in doi:
            doi = doi.split("doi.org/")[-1]
            
        # Determine canonical key (DOI > OpenAlex ID)
        canonical_key = doi if doi else openalex_id
        
        if not canonical_key:
            continue
            
        # Check if already exists
        if canonical_key in state.papers:
            continue
            
        # Create Paper object
        paper = Paper(
            title=title,
            year=year,
            venue=venue,
            openalex_id=openalex_id,
            doi=doi,
            citation_count=work.get("cited_by_count")
        )
        
        # Add to state
        state.papers[canonical_key] = paper
        newly_added.append(paper)
        
    return newly_added


def enrich_metadata_and_citations(agent, state: PipelineState) -> None:
    """
    Enrich metadata and build/extend the local citation graph.
    """
    logger.info("Enriching metadata via Semantic Scholar...")
    
    s2_detail_tool = SemanticScholarPaperDetailTool()
    s2_citation_tool = SemanticScholarCitationTool()
    
    # Limit enrichment to avoid rate limits (e.g. first 30 papers)
    papers_to_enrich = list(state.papers.values())[:30]
    
    for paper in papers_to_enrich:
        # Skip if already enriched (simple check: has fields_of_study)
        if paper.fields_of_study:
            continue
            
        # Determine ID to query
        paper_id = paper.doi if paper.doi else paper.openalex_id
        if not paper_id:
            continue
            
        try:
            res_json = s2_detail_tool.forward(paper_id=paper_id)
            if "Paper not found" in res_json:
                continue
                
            details = json.loads(res_json)
            
            # Update metadata
            if details.get("citation_count"):
                paper.citation_count = details["citation_count"]
            if details.get("s2_id"):
                paper.s2_id = details["s2_id"]
            if details.get("pdf_url"):
                paper.pdf_url = details["pdf_url"]
            
        except Exception as e:
            logger.warning(f"Failed to enrich paper {paper.title[:30]}...: {e}")
            
    # Optional: Expand citation graph for top-k papers
    sorted_papers = sorted(
        [p for p in state.papers.values() if p.citation_count is not None],
        key=lambda p: p.citation_count,
        reverse=True
    )
    
    for paper in sorted_papers[:3]:
        if not paper.s2_id:
            continue
        try:
            cit_json = s2_citation_tool.forward(
                paper_id=paper.s2_id,
                type="citations",
                limit=20
            )
            logger.debug(f"Fetched citations for top paper: {paper.title}")
        except Exception as e:
            logger.warning(f"Failed to fetch citations for {paper.title}: {e}")


def detect_method_families_from_text(text: str) -> List[str]:
    """
    Heuristically detect method families from the given text
    (title / abstract / PDF chunks). Returns a deduplicated
    list of normalized method family labels.
    """
    text_lower = text.lower()
    families = []
    
    # Network architectures
    if "transformer" in text_lower or " vit " in text_lower or "vision transformer" in text_lower:
        families.append("Transformer")
    if " cnn " in text_lower or "convolutional neural network" in text_lower or "resnet" in text_lower or " vgg" in text_lower:
        families.append("CNN")
    if "mamba" in text_lower or "state space model" in text_lower or " ssm " in text_lower:
        families.append("SSM/Mamba")
    if " rnn " in text_lower or "recurrent neural" in text_lower or " lstm " in text_lower or " gru " in text_lower:
        families.append("RNN")
    if " gnn " in text_lower or "graph neural network" in text_lower or "graph convolutional" in text_lower:
        families.append("GNN")
    if "swin" in text_lower and "transformer" in text_lower:
        families.append("Swin-Transformer")
    
    # Detection frameworks
    if "faster r-cnn" in text_lower or "faster rcnn" in text_lower:
        families.append("Faster R-CNN")
    if " yolo" in text_lower or "yolov" in text_lower or "yolo-v" in text_lower:
        families.append("YOLO-family")
    if " ssd " in text_lower or "single shot detector" in text_lower:
        families.append("SSD-family")
    if "retinanet" in text_lower:
        families.append("RetinaNet-family")
    if " detr" in text_lower or "deformable detr" in text_lower or "detection transformer" in text_lower:
        families.append("DETR-family")
    if "centernet" in text_lower or "center-based" in text_lower:
        families.append("CenterNet-family")
    if "anchor-free" in text_lower or "anchor free" in text_lower:
        families.append("Anchor-free")
    if "fcos" in text_lower:
        families.append("FCOS-family")
    
    # 3D / Geometric methods
    if ("bev" in text_lower or "bird's-eye view" in text_lower or "bird eye view" in text_lower) and "3d" in text_lower:
        families.append("BEV-based")
    if "nerf" in text_lower or "neural radiance field" in text_lower:
        families.append("NeRF")
    if "pointnet" in text_lower or "point-based" in text_lower:
        families.append("Point-based 3D")
    if "pointpillars" in text_lower or "point pillars" in text_lower:
        families.append("PointPillars")
    if "voxelnet" in text_lower or "voxel-based" in text_lower:
        families.append("Voxel-based 3D")
    
    # Generative / Representation learning
    if "diffusion model" in text_lower or "score-based" in text_lower or " ddpm" in text_lower or "denoising diffusion" in text_lower:
        families.append("Diffusion-model")
    if " vae " in text_lower or "variational autoencoder" in text_lower:
        families.append("VAE")
    if " gan " in text_lower or "generative adversarial" in text_lower:
        families.append("GAN")
    if "flow-based" in text_lower or "normalizing flow" in text_lower:
        families.append("Flow-based")
    if "autoregressive" in text_lower:
        families.append("Autoregressive")
    
    # Learning paradigms
    if "self-supervised" in text_lower or "self supervised" in text_lower:
        families.append("Self-supervised")
    if "contrastive learning" in text_lower or ("contrastive" in text_lower and "loss" in text_lower):
        families.append("Contrastive")
    if "distillation" in text_lower or "knowledge distillation" in text_lower:
        families.append("Distillation")
    if "multi-task" in text_lower or "multitask" in text_lower:
        families.append("Multi-task")
    if "multi-modal" in text_lower or "multimodal" in text_lower:
        families.append("Multi-modal")
    
    # Return unique families
    return list(set(families))


def maybe_parse_key_papers(agent, state: PipelineState, round_idx: int) -> None:
    """
    Optionally parse a few key PDFs to extract deeper signals.
    
    Now enabled from round 0 to ensure datasets/metrics/method families
    detection actually runs and provides useful information.
    """
    logger.debug(f"maybe_parse_key_papers called at round {round_idx}.")

    if not state.papers:
        logger.debug("No papers available for PDF parsing.")
        return

    # Select top-k papers by citation_count (fallback to 0)
    all_papers = list(state.papers.values())
    all_papers.sort(key=lambda p: p.citation_count or 0, reverse=True)
    key_papers = all_papers[:3]

    resolver = PaperResolverTool()
    parser = PdfParserTool()

    pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
    os.makedirs(pdf_cache_dir, exist_ok=True)

    for paper in key_papers:
        logger.info(f"[PDF] Processing key paper: {paper.title[:80]}...")
        
        # Resolve PDF URL if missing
        if not paper.pdf_url:
            try:
                result_str = resolver.forward(
                    doi=paper.doi,
                    arxiv_id=paper.arxiv_id,
                    title=paper.title if not paper.doi and not paper.arxiv_id else None,
                )
                res_data = json.loads(result_str)
                if res_data.get("pdf_url"):
                    paper.pdf_url = res_data["pdf_url"]
            except Exception as e:
                logger.warning(f"Failed to resolve PDF for {paper.title}: {e}")

        if not paper.pdf_url:
            logger.warning(f"[PDF] No PDF URL found for paper: {paper.title[:80]}")
            continue

        # Download PDF to local cache
        local_filename = f"{paper.doi.replace('/', '_') if paper.doi else paper.openalex_id}.pdf"
        local_pdf_path = os.path.join(pdf_cache_dir, local_filename)
        
        if not os.path.exists(local_pdf_path):
            try:
                logger.info(f"Downloading PDF from {paper.pdf_url}...")
                response = requests.get(paper.pdf_url, timeout=30)
                response.raise_for_status()
                with open(local_pdf_path, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                logger.warning(f"[PDF] Failed to download PDF for paper: {paper.title[:80]} - {e}")
                continue
        
        paper.local_pdf_path = local_pdf_path

        # Parse the PDF into chunks
        try:
            logger.info(f"Parsing PDF: {local_pdf_path}")
            parser_result_str = parser.forward(pdf_path=local_pdf_path, chunk_size=1000)
            
            # Extract text from chunks
            content_json = json.loads(parser_result_str)
            
            # Populate paper.chunks
            paper.chunks = [
                Chunk(
                    text=chunk["text"],
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end")
                )
                for chunk in content_json
            ]
            
            full_text = " ".join([chunk.text for chunk in paper.chunks])
            
            # Dataset detection
            common_datasets = ["kitti", "nuscenes", "waymo", "coco", "imagenet", "cityscapes", 
                             "nyu", "sun rgb-d", "scannet", "ade20k"]
            found_datasets = [d for d in common_datasets if d in full_text.lower()]
            
            if found_datasets:
                logger.info(f"Found datasets in {paper.title}: {found_datasets}")
                # Update global stats
                for ds in found_datasets:
                    if ds not in state.datasets:
                        state.datasets[ds] = {"count": 0, "papers": []}
                    state.datasets[ds]["count"] += 1
                    state.datasets[ds]["papers"].append(paper.title)
                # Update per-paper features
                paper.datasets = list(set(paper.datasets + found_datasets))
            
            # Metrics detection
            common_metrics = [
                "map", "ap", "iou", "ap_3d", "ap_bev", "aos",  # detection
                "abs_rel", "rmse", "δ1", "δ2", "δ3", "delta1", "delta2", "delta3",  # depth
                "miou", "dice", "f1", "accuracy", "precision", "recall",  # segmentation/classification
                "psnr", "ssim", "lpips",  # image quality
                "fid", "is", "inception score"  # generative models
            ]
            
            # Normalize text for better matching
            text_lower = full_text.lower()
            found_metrics = []
            for metric in common_metrics:
                # Match whole words or with common separators
                if f" {metric} " in text_lower or f"_{metric}_" in text_lower or f"{metric}@" in text_lower:
                    found_metrics.append(metric)
            
            if found_metrics:
                logger.info(f"Found metrics in {paper.title}: {found_metrics}")
                # Update global stats
                for metric in found_metrics:
                    metric_key = f"metric_{metric}"
                    if metric_key not in state.datasets:
                        state.datasets[metric_key] = {"count": 0, "papers": []}
                    state.datasets[metric_key]["count"] += 1
                    state.datasets[metric_key]["papers"].append(paper.title)
                    
                    # Also add to paper tags for easier access
                    if metric not in paper.tags:
                        paper.tags.append(f"metric:{metric}")
                # Update per-paper features
                paper.metrics = list(set(paper.metrics + found_metrics))
            
            # Method families detection (new)
            # Combine title + first portion of PDF text for better detection
            combined_text = f"{paper.title} {full_text[:5000]}"  # Use first 5000 chars
            found_families = detect_method_families_from_text(combined_text)
            
            if found_families:
                logger.info(f"Found method families in {paper.title}: {found_families}")
                # Merge with existing families and deduplicate
                paper.method_families = list(set(paper.method_families + found_families))
                    
        except Exception as e:
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")
