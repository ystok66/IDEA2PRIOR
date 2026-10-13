# pipelines/run_manager.py

"""
Run-oriented directory management for Prior Art Check pipeline.

Each run gets its own directory under `runs/` containing:
- config.json: Run configuration
- search_log.jsonl: Step-by-step search log
- md_reports/: Generated Markdown reports
- pdfs/: Symlinks/copies of PDFs used in this run
- parsed/: Serialized PaperRecord JSON files
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from pipelines.runtime import (
    LLMRuntime, RunLedger, atomic_write_bytes, atomic_write_json, append_jsonl,
    redact, secret_values,
)


class RunManager:
    """
    Manages run-specific directory structure and file operations.
    
    Usage:
        run_mgr = RunManager(topic="monocular 3D object detection")
        run_mgr.create_run_directory()
        run_mgr.save_config({"topic": "...", "year_start": 2018, ...})
        run_mgr.append_search_log("seed_queries", {"queries": [...]})
    """
    
    def __init__(
        self,
        topic: str,
        timestamp: Optional[str] = None,
        base_dir: Optional[Path] = None,
        resume_run_dir: Optional[Path] = None,
        shared_pdf_cache_dir: Optional[Path] = None,
    ):
        """
        Initialize RunManager.
        
        Args:
            topic: Research topic (will be slugified for directory name)
            timestamp: Optional timestamp string (defaults to current time)
            base_dir: Base directory for runs (defaults to project root)
            shared_pdf_cache_dir: Optional exact-byte parsed-page cache shared
                across runs. Downloaded PDFs and source bindings remain local
                to base_dir; this directory never resolves a remote document.
        """
        self.topic = topic
        self.timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Determine base directory (project root)
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            # Default: project root (parent of pipelines/)
            self.base_dir = Path(__file__).resolve().parent.parent
        
        # Create slugified topic
        self.topic_slug = self._slugify(topic)
        
        # Build run ID and paths
        self.run_id = f"{self.timestamp}_{self.topic_slug}_{uuid.uuid4().hex[:12]}"
        self.run_dir = self.base_dir / "runs" / self.run_id
        self._resuming = resume_run_dir is not None
        if resume_run_dir is not None:
            self.run_dir = Path(resume_run_dir).resolve()
            if not self.run_dir.is_dir():
                raise ValueError("Explicit resume directory does not exist")
            self.run_id = self.run_dir.name
            saved_config = self.run_dir / "config.json"
            if saved_config.is_file():
                previous = json.loads(saved_config.read_text(encoding="utf-8"))
                if previous.get("topic", topic) != topic:
                    raise ValueError("Resume topic differs from the saved run")
                self.timestamp = previous.get("timestamp", self.timestamp)
                self.topic_slug = previous.get("topic_slug", self.topic_slug)
            if base_dir is None:
                self.base_dir = self.run_dir.parent.parent
        
        # Subdirectories
        self.md_reports_dir = self.run_dir / "md_reports"
        self.pdfs_dir = self.run_dir / "pdfs"
        self.parsed_dir = self.run_dir / "parsed"
        
        # Source-bound PDF bytes stay with base_dir. Only exact-byte parsed pages
        # may use the separately selected shared cache.
        self.pdf_cache_dir = self.base_dir / "pdf_cache"
        self.pdf_pages_cache_dir = (
            Path(shared_pdf_cache_dir).resolve()
            if shared_pdf_cache_dir is not None else self.pdf_cache_dir
        )
        
        self._initialized = False
        self._initialize_lock = threading.RLock()
        self._runtime = None
        self._secrets = ()
    
    @staticmethod
    def _slugify(text: str, max_length: int = 50) -> str:
        """Convert text to a safe directory name."""
        # Lowercase, replace non-alphanumeric with underscore
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower())
        # Remove leading/trailing underscores
        slug = slug.strip("_")
        # Truncate if too long
        return slug[:max_length]
    
    def create_run_directory(self) -> Path:
        """
        Create the run directory and all subdirectories.
        
        Returns:
            Path to the run directory
        """
        with self._initialize_lock:
            if self._initialized:
                return self.run_dir
            # Never silently merge two new runs, including identical timestamps.
            self.run_dir.mkdir(parents=True, exist_ok=self._resuming)
            self.md_reports_dir.mkdir(exist_ok=True)
            self.pdfs_dir.mkdir(exist_ok=True)
            self.parsed_dir.mkdir(exist_ok=True)
            self.pdf_cache_dir.mkdir(parents=True, exist_ok=True)
            self.pdf_pages_cache_dir.mkdir(parents=True, exist_ok=True)
            self._initialized = True
        logger.info(f"[RunManager] Created run directory: {self.run_dir}")
        
        return self.run_dir

    @property
    def runtime(self) -> LLMRuntime:
        """The run's single in-process coordinator; share across all workers."""
        self.create_run_directory()
        with self._initialize_lock:
            if self._runtime is None:
                self._runtime = LLMRuntime(RunLedger(self.run_dir))
                self._runtime.ledger.secrets = self._secrets
            return self._runtime
    
    def save_config(self, config: Dict[str, Any]) -> Path:
        """
        Save configuration to config.json in the run directory.
        
        Args:
            config: Dictionary of configuration values
            
        Returns:
            Path to the saved config file
        """
        self.create_run_directory()
        
        config_path = self.run_dir / "config.json"
        
        # Add run metadata
        full_config = {
            **config,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "topic": self.topic,
            "topic_slug": self.topic_slug,
        }
        self._secrets = tuple(set(self._secrets + secret_values(full_config)))
        if self._resuming and config_path.exists():
            previous = json.loads(config_path.read_text(encoding="utf-8"))
            if previous != redact(full_config, self._secrets):
                raise ValueError("Resume config differs; use a new run for changed inputs/config")
        atomic_write_json(config_path, redact(full_config, self._secrets))
        self.runtime.ledger.write_manifest(full_config)
        
        logger.debug(f"[RunManager] Saved config to {config_path}")
        return config_path
    
    def append_search_log(self, step: str, payload: Dict[str, Any]) -> None:
        """
        Append a log entry to search_log.jsonl.
        
        Args:
            step: Name of the step (e.g., "seed_queries", "main_bucket_search")
            payload: Data to log for this step
        """
        self.create_run_directory()
        
        log_path = self.run_dir / "search_log.jsonl"
        
        entry = {
            "step": step,
            "timestamp": datetime.now().isoformat(),
            "payload": payload
        }
        
        append_jsonl(log_path, redact(entry, self._secrets))
    
    def get_pdf_path(self, paper_id: str) -> Path:
        """
        Get the path for a PDF in the run-local pdfs/ directory.
        
        Args:
            paper_id: Paper identifier (will be sanitized)
            
        Returns:
            Path to the PDF in the run directory
        """
        safe_id = self._slugify(paper_id, max_length=100)
        return self.pdfs_dir / f"{safe_id}.pdf"
    
    def get_global_pdf_path(self, paper_id: str) -> Path:
        """
        Get the path for a PDF in the global pdf_cache/ directory.
        
        Args:
            paper_id: Paper identifier (will be sanitized)
            
        Returns:
            Path to the PDF in the global cache
        """
        safe_id = self._slugify(paper_id, max_length=100)
        return self.pdf_cache_dir / f"{safe_id}.pdf"
    
    def link_pdf_to_run(self, paper_id: str, global_pdf_path: Optional[Path] = None) -> Optional[Path]:
        """
        Create a link from run-local pdfs/ to global pdf_cache/.
        
        On Windows, creates a copy instead of symlink (symlinks require admin).
        
        Args:
            paper_id: Paper identifier
            global_pdf_path: Optional path to the global PDF (defaults to get_global_pdf_path)
            
        Returns:
            Path to the run-local PDF, or None if global PDF doesn't exist
        """
        self.create_run_directory()
        
        if global_pdf_path is None:
            global_pdf_path = self.get_global_pdf_path(paper_id)
        
        if not global_pdf_path.exists():
            return None
        
        run_pdf_path = self.get_pdf_path(paper_id)
        
        if run_pdf_path.exists():
            return run_pdf_path
        
        try:
            # Try symlink first (faster, saves space)
            run_pdf_path.symlink_to(global_pdf_path)
        except OSError:
            # Fallback to copy on Windows without admin rights
            shutil.copy2(global_pdf_path, run_pdf_path)
        
        return run_pdf_path
    
    def get_parsed_path(self, paper_id: str) -> Path:
        """
        Get the path for a parsed PaperRecord JSON in the run directory.
        
        Args:
            paper_id: Paper identifier (will be sanitized)
            
        Returns:
            Path to the parsed JSON file
        """
        safe_id = self._slugify(paper_id, max_length=100)
        return self.parsed_dir / f"{safe_id}.json"
    
    def get_report_path(self, filename: str) -> Path:
        """
        Get the path for a report in the md_reports/ directory.
        
        Args:
            filename: Report filename (e.g., "prior_art_report.md")
            
        Returns:
            Path to the report file
        """
        self.create_run_directory()
        return self.md_reports_dir / filename
    
    def save_report(self, content: str, filename: Optional[str] = None) -> Path:
        """
        Save a Markdown report to the run directory.
        
        Args:
            content: Report content
            filename: Optional filename (defaults to prior_art_{topic_slug}_{timestamp}.md)
            
        Returns:
            Path to the saved report
        """
        if filename is None:
            filename = f"prior_art_{self.topic_slug}_{self.timestamp}.md"
        
        report_path = self.get_report_path(filename)
        
        atomic_write_bytes(report_path, content.replace("\\n", "\n").encode("utf-8"))
        
        logger.info(f"[RunManager] Saved report to {report_path}")
        return report_path
