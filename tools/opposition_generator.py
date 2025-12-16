"""
Opposition Generator Tool for smolagents
Automatically generates counter-arguments and critical viewpoints for summaries or conclusions.
Challenges claims by suggesting alternative explanations or weaknesses.
"""

import os
from typing import Optional
from smolagents import Tool
from loguru import logger

from pipelines.llm_config import get_litellm_config
from smolagents import LiteLLMModel


class OppositionGeneratorTool(Tool):
    """
    Tool for generating counter-arguments and critical viewpoints.
    
    This tool takes a summary, conclusion, or claim and automatically generates:
    - Counter-arguments
    - Critical viewpoints
    - Alternative explanations
    - Potential weaknesses or limitations
    """
    
    name = "opposition_generator"
    description = (
        "Generate counter-arguments and critical viewpoints for a given summary, conclusion, or claim. "
        "This tool challenges claims by suggesting alternative explanations, weaknesses, limitations, "
        "or opposing perspectives. Useful for critical analysis and identifying potential flaws in arguments."
    )
    inputs = {
        "content": {
            "type": "string",
            "description": "The summary, conclusion, or claim to analyze and generate opposition for.",
        },
        "context": {
            "type": "string",
            "description": "Optional context or domain information to guide the opposition generation (e.g., 'machine learning research', 'medical study').",
            "nullable": True,
        },
        "focus_areas": {
            "type": "string",
            "description": "Optional comma-separated list of specific areas to focus criticism on (e.g., 'methodology, assumptions, evidence'). Leave empty for comprehensive analysis.",
            "nullable": True,
        },
    }
    output_type = "string"
    
    def __init__(self, model_id: Optional[str] = None, api_base: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize the opposition generator tool.
        
        Args:
            model_id: LLM model ID (defaults to heavy model from config)
            api_base: API base URL (defaults to config)
            api_key: API key (defaults to config)
        """
        super().__init__()
        
        # Use provided config or fall back to LLM config system
        if model_id and api_base and api_key:
            self.model_id = model_id
            self.api_base = api_base
            self.api_key = api_key
        else:
            # Use heavy model for complex reasoning tasks
            self.model_id, self.api_base, self.api_key = get_litellm_config(role="heavy")
        
        # Initialize LLM model
        self.model = LiteLLMModel(
            model_id=self.model_id,
            api_base=self.api_base,
            api_key=self.api_key,
        )
        
        logger.info(f"🔧 OppositionGeneratorTool initialized with model: {self.model_id}")
    
    def forward(
        self, 
        content: str, 
        context: Optional[str] = None,
        focus_areas: Optional[str] = None
    ) -> str:
        """
        Generate counter-arguments and critical viewpoints.
        
        Args:
            content: The summary, conclusion, or claim to analyze
            context: Optional context or domain information
            focus_areas: Optional specific areas to focus on
            
        Returns:
            Formatted string with counter-arguments and critical analysis
        """
        try:
            # Use default max_tokens of 800 for brief analysis when called via agent
            max_tokens = 800
            
            # Build the prompt (pass max_tokens to adjust prompt style)
            prompt = self._create_opposition_prompt(content, context, focus_areas, max_tokens)
            
            # Call LLM - try to pass max_tokens if the model supports it
            messages = [{"role": "user", "content": prompt}]
            try:
                response = self.model(messages, max_tokens=max_tokens)
            except TypeError:
                # If model doesn't support max_tokens parameter, call without it
                response = self.model(messages)
            
            logger.info("✅ Opposition generation completed")
            return response.content if hasattr(response, 'content') else str(response)
            
        except Exception as e:
            error_msg = f"❌ Failed to generate opposition: {str(e)}"
            logger.error(error_msg)
            return error_msg
    
    def _create_opposition_prompt(
        self, 
        content: str, 
        context: Optional[str] = None,
        focus_areas: Optional[str] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Create the prompt for opposition generation.
        
        Args:
            content: The content to analyze
            context: Optional context
            focus_areas: Optional focus areas
            max_tokens: Maximum tokens for response (used to adjust prompt)
            
        Returns:
            Formatted prompt string
        """
        # Determine if we want a brief or comprehensive analysis
        is_brief = max_tokens is not None and max_tokens < 1000
        
        prompt = """You are a critical analyst tasked with generating counter-arguments and critical viewpoints.

Your goal is to challenge the given claim, summary, or conclusion by:
1. Identifying potential weaknesses or limitations
2. Suggesting alternative explanations or interpretations
3. Highlighting assumptions that may not hold
4. Pointing out gaps in evidence or reasoning
5. Proposing opposing perspectives or viewpoints

Be constructive and analytical, not merely contrarian. Focus on legitimate concerns and valid alternative perspectives.

"""
        
        if context:
            prompt += f"**Context/Domain:** {context}\n\n"
        
        if focus_areas:
            areas = [area.strip() for area in focus_areas.split(",")]
            prompt += f"**Focus Areas:** Please pay special attention to: {', '.join(areas)}\n\n"
        
        if is_brief:
            prompt += f"""**Content to Analyze:**
{content}

**Your Task:**
Generate a BRIEF critical analysis (keep it concise, under {max_tokens} tokens) with the following structure:

1. **Counter-Arguments**: List 2-3 main counter-arguments (1-2 sentences each)
2. **Alternative Explanations**: Suggest 1-2 alternative interpretations (1 sentence each)
3. **Potential Weaknesses**: Identify 2-3 key methodological or evidential weaknesses (1 sentence each)
4. **Limitations**: Discuss 1-2 key scope limitations (1 sentence each)

Format your response clearly with section headers. Be concise and specific. Avoid lengthy explanations."""
        else:
            prompt += f"""**Content to Analyze:**
{content}

**Your Task:**
Generate a comprehensive critical analysis with the following structure:

1. **Counter-Arguments**: List 3-5 main counter-arguments or opposing viewpoints
2. **Alternative Explanations**: Suggest 2-3 alternative interpretations or explanations
3. **Potential Weaknesses**: Identify methodological, logical, or evidential weaknesses
4. **Unstated Assumptions**: Highlight implicit assumptions that may be questionable
5. **Limitations**: Discuss scope limitations or boundary conditions
6. **Questions for Further Investigation**: Pose critical questions that should be addressed

Format your response clearly with section headers. Be specific and constructive."""
        
        return prompt


def generate_opposition_standalone(
    content: str,
    context: Optional[str] = None,
    focus_areas: Optional[str] = None,
    model_id: Optional[str] = None,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: Optional[int] = 800
) -> str:
    """
    Standalone function to generate opposition without creating a tool instance.
    
    This can be used in pipelines or other contexts where you don't need a Tool object.
    
    Args:
        content: The content to analyze
        context: Optional context
        focus_areas: Optional focus areas
        model_id: Optional model ID (defaults to config)
        api_base: Optional API base (defaults to config)
        api_key: Optional API key (defaults to config)
        max_tokens: Maximum tokens for response (default: 800 for brief analysis)
        
    Returns:
        Generated opposition analysis
    """
    tool = OppositionGeneratorTool(model_id=model_id, api_base=api_base, api_key=api_key)
    # Note: max_tokens is handled internally in forward() method (hardcoded to 800)
    # We pass it to _create_opposition_prompt to adjust the prompt
    return tool.forward(content=content, context=context, focus_areas=focus_areas)


if __name__ == "__main__":
    """Test the opposition generator tool."""
    import sys
    
    # Test content
    test_content = """
    Machine learning models trained on large datasets have shown remarkable performance 
    in various tasks. Deep neural networks, particularly transformers, have revolutionized 
    natural language processing and computer vision. The success of these models suggests 
    that scale is the primary factor in achieving state-of-the-art results.
    """
    
    try:
        print("🚀 Testing Opposition Generator Tool...")
        print("=" * 60)
        print("Input Content:")
        print(test_content)
        print("=" * 60)
        
        tool = OppositionGeneratorTool()
        result = tool.forward(
            content=test_content,
            context="machine learning research",
            focus_areas="assumptions, evidence, methodology"
        )
        
        print("\n📊 Generated Opposition:")
        print("=" * 60)
        print(result)
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

