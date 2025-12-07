#!/usr/bin/env python3
"""
Planner-Worker Agent Full Feature Demo
Includes: Evolution Map and Code Extractor feature tests
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "paperarena_hub"))

load_dotenv()

from loguru import logger
from smolagents import OpenAIServerModel
from smolagents.models import REMOVE_PARAMETER

from paperarena_hub.planner_worker import PlannerWorkerAgent
from paperarena_hub.tools.pdf_extractor import PDFExtractorTool, PDFExtractorRAGTool
from paperarena_hub.tools.semantic_scholar_tool import SemanticScholarTool
from paperarena_hub.tools.code_extractor import CodeExtractorTool

# Configure logging
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
)


def setup_agent() -> PlannerWorkerAgent:
    """Setup and create PlannerWorkerAgent"""
    
    logger.info("🔧 Initializing PlannerWorkerAgent...")
    
    # 1. Create main model (Gemini API)
    gemini_api_key = os.getenv("OPENAI_API_KEY", "AIzaSyCvJE0q0EgejGg1nro3ZIBNehcHM01VwV0")
    gemini_api_base = os.getenv("OPENAI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    gemini_model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-exp")
    
    model = OpenAIServerModel(
        model_id=gemini_model_name,
        api_base=gemini_api_base,
        api_key=gemini_api_key,
        stop=REMOVE_PARAMETER
    )
    
    logger.info(f"   ✅ Main model: {gemini_model_name}")
    
    # 2. Configure Embedding API (OpenAI GPT API)
    embedding_api_key = os.getenv("OPENAI_EMBEDDING_API_KEY")
    if embedding_api_key:
        logger.info("   ✅ Embedding API: OpenAI GPT")
    else:
        logger.warning("   ⚠️ Embedding API key not set")
    
    # 3. Initialize tools
    semantic_tool = SemanticScholarTool(
        api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
        output_dir=project_root / "paperarena" / "sampled_jsons"
    )
    semantic_tool.setup()
    
    pdf_extractor = PDFExtractorRAGTool(
        pdf_base_dir=project_root / "paperarena" / "sampled_papers",
        output_base_dir=project_root / "paperarena" / "sampled_jsons",
        embedding_api_key=embedding_api_key
    )
    
    code_extractor = CodeExtractorTool(
        pdf_extractor=pdf_extractor,
        pdf_base_dir=project_root / "paperarena" / "sampled_papers",
        output_base_dir=project_root / "paperarena" / "sampled_jsons",
        use_mock=True,
        llm_model_name=gemini_model_name
    )
    code_extractor.setup()
    
    tools = [
        PDFExtractorTool(
            pdf_base_dir=project_root / "paperarena" / "sampled_papers",
            output_base_dir=project_root / "paperarena" / "sampled_jsons"
        ),
        pdf_extractor,
        semantic_tool,
        code_extractor,
    ]
    
    # Add EvolutionMapper
    try:
        from paperarena_hub.tools.evolution_mapper import EvolutionMapper
        from paperarena_hub.tools.database_search import DatabaseSearch
        
        database_search = DatabaseSearch(
            database_dir=project_root / "paperarena" / "qa"
        )
        database_search.setup()
        tools.append(database_search)
        
        evolution_mapper = EvolutionMapper(
            semantic_tool=semantic_tool,
            database_search=database_search,
            output_dir=project_root / "paperarena" / "sampled_jsons"
        )
        tools.append(evolution_mapper)
        logger.info("   ✅ EvolutionMapper added")
    except Exception as e:
        logger.warning(f"   ⚠️ EvolutionMapper unavailable: {e}")
    
    # 4. Create Agent
    agent = PlannerWorkerAgent(
        model=model,
        tools=tools,
        max_steps=20,
        enable_reflection=True,
        enable_critic=True,  # Enable Critic module
        max_reexecutions=2  # Maximum 2 re-executions allowed
    )
    
    logger.info(f"   ✅ Agent initialization complete, {len(tools)} tools available")
    return agent


def test_evolution_map(agent: PlannerWorkerAgent):
    """Test Evolution Map generation"""
    
    print("=" * 80)
    print("🧪 Test 1: Generate Evolution Map")
    print("=" * 80)
    print()
    
    task = """
    Please help me generate a technology evolution map for Large Language Models.
    
    Requirements:
    1. Use the evolution_mapper tool
    2. Include timeline of major papers (e.g., GPT, BERT, T5, LLaMA, ChatGPT, etc.)
    3. Show citation relationships
    4. Identify technology branches (e.g., autoregressive models, bidirectional encoders, instruction tuning, RLHF, etc.)
    5. Generate Mermaid format diagram
    6. Use a smaller max_papers parameter (suggest 10 papers) to speed up testing
    
    Please use appropriate tools to complete this task.
    """
    
    print("📝 Task description:")
    print(task)
    print()
    print("=" * 80)
    print("Starting execution...")
    print("=" * 80)
    print()
    
    try:
        result = agent.run(task=task)
        
        print()
        print("=" * 80)
        print("📊 Execution Results")
        print("=" * 80)
        print()
        
        print(f"✅ Execution status: {'Success' if result['success'] else 'Failed'}")
        print(f"⏱️  Total time: {result['total_time']:.2f} seconds")
        print()
        
        # Display plan
        plan = result['plan']
        print("📋 Generated Plan:")
        print("-" * 80)
        print(f"Task analysis: {plan.analysis[:200]}...")
        print()
        print(f"Selected tools ({len(plan.selected_tools)}):")
        for i, tool_sel in enumerate(plan.selected_tools, 1):
            print(f"  {i}. {tool_sel.tool_name}")
            print(f"     Reason: {tool_sel.reason[:100]}...")
        print()
        
        # Display execution results
        execution = result['execution']
        print("🔧 Execution Details:")
        print("-" * 80)
        for i, step_result in enumerate(execution['execution_steps'], 1):
            print(f"Step {i}: {step_result['tool_name']}")
            print(f"  Status: {'✅ Success' if step_result['success'] else '❌ Failed'}")
            if step_result.get('result'):
                result_str = str(step_result['result'])
                result_length = step_result.get('result_length', len(result_str))
                print(f"  Result length: {result_length} characters")
                # For evolution_mapper, display full result (includes Mermaid diagram)
                if step_result['tool_name'] == 'evolution_mapper':
                    print(f"  Full result:\n{result_str}")
                else:
                    # Other tools show first 1000 characters
                    result_preview = result_str[:1000]
                    print(f"  Result preview:\n{result_preview}")
                    if len(result_str) > 1000:
                        print(f"  ... (Total {len(result_str)} characters, full result saved to file)")
            print()
        
        # Display Critic evaluation results (if available)
        if result.get('evaluation'):
            evaluation = result['evaluation']
            print("🔍 Critic Evaluation Results:")
            print("-" * 80)
            print(f"  Quality score: {evaluation.quality_score:.2f}/1.0")
            print(f"  Completeness score: {evaluation.completeness_score:.2f}/1.0")
            print(f"  Correctness score: {evaluation.correctness_score:.2f}/1.0")
            print(f"  Meets requirements: {'✅ Yes' if evaluation.meets_requirements else '❌ No'}")
            
            if evaluation.strengths:
                print(f"\n  ✅ Strengths ({len(evaluation.strengths)}):")
                for strength in evaluation.strengths:
                    print(f"     - {strength}")
            
            if evaluation.weaknesses:
                print(f"\n  ⚠️ Weaknesses ({len(evaluation.weaknesses)}):")
                for weakness in evaluation.weaknesses:
                    print(f"     - {weakness}")
            
            if evaluation.improvements:
                print(f"\n  💡 Improvement suggestions ({len(evaluation.improvements)}):")
                for improvement in evaluation.improvements:
                    print(f"     - {improvement}")
            
            if evaluation.overall_feedback:
                print(f"\n  📝 Overall feedback:")
                print(f"     {evaluation.overall_feedback}")
            
            if evaluation.needs_reexecution:
                print(f"\n  🔄 Re-execution needed: Yes")
                if evaluation.reexecution_reason:
                    print(f"     Reason: {evaluation.reexecution_reason}")
            
            print()
        
        if result.get('reexecution_count', 0) > 0:
            print(f"🔄 Re-execution count: {result['reexecution_count']}")
            print()
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_code_extractor(agent: PlannerWorkerAgent):
    """Test Code Extractor"""
    
    print("=" * 80)
    print("🧪 Test 2: Code Extractor")
    print("=" * 80)
    print()
    
    task = """
    Please help me analyze the code reproducibility of a paper.
    
    Requirements:
    1. Use the code_extractor tool
    2. Analyze code and pseudocode in the paper
    3. Check code completeness
    4. Evaluate reproducibility
    5. Classify the paper (📗 Complete code / 📙 Partial code / 📕 No code)
    
    Please use test_paper as the test paper (if it exists).
    If it doesn't exist, please first search for a paper containing code, then analyze it.
    """
    
    print("📝 Task description:")
    print(task)
    print()
    print("=" * 80)
    print("Starting execution...")
    print("=" * 80)
    print()
    
    try:
        result = agent.run(task=task)
        
        print()
        print("=" * 80)
        print("📊 Execution Results")
        print("=" * 80)
        print()
        
        print(f"✅ Execution status: {'Success' if result['success'] else 'Failed'}")
        print(f"⏱️  Total time: {result['total_time']:.2f} seconds")
        print()
        
        # Display plan
        plan = result['plan']
        print("📋 Generated Plan:")
        print("-" * 80)
        print(f"Task analysis: {plan.analysis[:200]}...")
        print()
        print(f"Selected tools ({len(plan.selected_tools)}):")
        for i, tool_sel in enumerate(plan.selected_tools, 1):
            print(f"  {i}. {tool_sel.tool_name}")
            print(f"     Reason: {tool_sel.reason[:100]}...")
        print()
        
        # Display execution results
        execution = result['execution']
        print("🔧 Execution Details:")
        print("-" * 80)
        for i, step_result in enumerate(execution['execution_steps'], 1):
            print(f"Step {i}: {step_result['tool_name']}")
            print(f"  Status: {'✅ Success' if step_result['success'] else '❌ Failed'}")
            if step_result.get('result'):
                result_str = str(step_result['result'])
                result_length = step_result.get('result_length', len(result_str))
                print(f"  Result length: {result_length} characters")
                # For code_extractor, display full result (includes detailed report)
                if step_result['tool_name'] == 'code_extractor':
                    print(f"  Full result:\n{result_str}")
                else:
                    # Other tools show first 1000 characters
                    result_preview = result_str[:1000]
                    print(f"  Result preview:\n{result_preview}")
                    if len(result_str) > 1000:
                        print(f"  ... (Total {len(result_str)} characters, full result saved to file)")
            if step_result.get('error'):
                print(f"  Error: {step_result['error']}")
            print()
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main function"""
    
    print("=" * 80)
    print("🚀 Planner-Worker Agent Full Feature Demo")
    print("=" * 80)
    print()
    
    # Setup Agent
    agent = setup_agent()
    
    results = {}
    
    # Test 1: Evolution Map
    print("\n" + "=" * 80)
    print("Test 1: Generate Evolution Map")
    print("=" * 80)
    result1 = test_evolution_map(agent)
    results["evolution_map"] = result1
    
    # Test 2: Code Extractor
    print("\n" + "=" * 80)
    print("Test 2: Code Extractor")
    print("=" * 80)
    result2 = test_code_extractor(agent)
    results["code_extractor"] = result2
    
    # Save results
    output_dir = Path("./history") / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    result_file = output_dir / "planner_worker_full_demo_result.md"
    with open(result_file, 'w', encoding='utf-8') as f:
        f.write("# Planner-Worker Agent Full Feature Demo Results\n\n")
        f.write(f"**Execution time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # Evolution Map results
        if result1:
            f.write("## Test 1: Evolution Map\n\n")
            f.write(f"**Execution status**: {'Success' if result1['success'] else 'Failed'}\n\n")
            f.write(f"**Total time**: {result1['total_time']:.2f} seconds\n\n")
            f.write("### Generated Plan\n\n")
            f.write(f"**Task analysis**: {result1['plan'].analysis}\n\n")
            f.write("**Selected tools**:\n\n")
            for i, tool_sel in enumerate(result1['plan'].selected_tools, 1):
                f.write(f"{i}. **{tool_sel.tool_name}**\n")
                f.write(f"   - Reason: {tool_sel.reason}\n")
                f.write(f"   - Parameters: {tool_sel.parameters}\n\n")
            f.write("### Execution Results\n\n")
            
            # Display all execution attempts (including re-executions)
            if result1.get('all_executions'):
                f.write(f"**Execution attempts**: {len(result1['all_executions'])}\n\n")
                for exec_idx, execution in enumerate(result1['all_executions'], 1):
                    if len(result1['all_executions']) > 1:
                        f.write(f"#### Execution Attempt {exec_idx}\n\n")
                    for i, step_result in enumerate(execution['execution_steps'], 1):
                        f.write(f"##### Step {i}: {step_result['tool_name']}\n\n")
                        f.write(f"**Status**: {'✅ Success' if step_result['success'] else '❌ Failed'}\n\n")
                        if step_result.get('result'):
                            result_str = str(step_result['result'])
                            result_length = step_result.get('result_length', len(result_str))
                            f.write(f"**Result length**: {result_length} characters\n\n")
                            # Save full result
                            f.write(f"**Full result**:\n\n")
                            # If it's a Mermaid diagram, use mermaid code block
                            if '```mermaid' in result_str or ('graph' in result_str[:200] and 'TD' in result_str[:200]):
                                f.write(f"{result_str}\n\n")
                            elif 'graph TD' in result_str or 'graph LR' in result_str or 'flowchart' in result_str:
                                f.write(f"{result_str}\n\n")
                            else:
                                f.write(f"```\n{result_str}\n```\n\n")
            else:
                # Compatible with old format
                for i, step_result in enumerate(result1['execution']['execution_steps'], 1):
                    f.write(f"#### Step {i}: {step_result['tool_name']}\n\n")
                    f.write(f"**Status**: {'✅ Success' if step_result['success'] else '❌ Failed'}\n\n")
                    if step_result.get('result'):
                        result_str = str(step_result['result'])
                        result_length = step_result.get('result_length', len(result_str))
                        f.write(f"**Result length**: {result_length} characters\n\n")
                        # Save full result
                        f.write(f"**Full result**:\n\n")
                        # If it's a Mermaid diagram, use mermaid code block
                        if '```mermaid' in result_str or ('graph' in result_str[:200] and 'TD' in result_str[:200]):
                            f.write(f"{result_str}\n\n")
                        elif 'graph TD' in result_str or 'graph LR' in result_str or 'flowchart' in result_str:
                            f.write(f"{result_str}\n\n")
                        else:
                            f.write(f"```\n{result_str}\n```\n\n")
            
            # Save Critic evaluation results
            if result1.get('evaluation'):
                eval = result1['evaluation']
                f.write("### 🔍 Critic Evaluation Results\n\n")
                f.write(f"- **Quality score**: {eval.quality_score:.2f}/1.0\n")
                f.write(f"- **Completeness score**: {eval.completeness_score:.2f}/1.0\n")
                f.write(f"- **Correctness score**: {eval.correctness_score:.2f}/1.0\n")
                f.write(f"- **Meets requirements**: {'✅ Yes' if eval.meets_requirements else '❌ No'}\n\n")
                
                if eval.strengths:
                    f.write(f"#### ✅ Strengths\n\n")
                    for strength in eval.strengths:
                        f.write(f"- {strength}\n")
                    f.write("\n")
                
                if eval.weaknesses:
                    f.write(f"#### ⚠️ Weaknesses\n\n")
                    for weakness in eval.weaknesses:
                        f.write(f"- {weakness}\n")
                    f.write("\n")
                
                if eval.improvements:
                    f.write(f"#### 💡 Improvement Suggestions\n\n")
                    for improvement in eval.improvements:
                        f.write(f"- {improvement}\n")
                    f.write("\n")
                
                if eval.overall_feedback:
                    f.write(f"#### 📝 Overall Feedback\n\n")
                    f.write(f"{eval.overall_feedback}\n\n")
                
                if eval.needs_reexecution:
                    f.write(f"#### 🔄 Re-execution Suggestion\n\n")
                    f.write(f"- **Re-execution needed**: Yes\n")
                    if eval.reexecution_reason:
                        f.write(f"- **Reason**: {eval.reexecution_reason}\n")
                    f.write("\n")
            
            if result1.get('reexecution_count', 0) > 0:
                f.write(f"### 🔄 Re-execution\n\n")
                f.write(f"- **Re-execution count**: {result1['reexecution_count']}\n\n")
        
        # Code Extractor results
        if result2:
            f.write("\n## Test 2: Code Extractor\n\n")
            f.write(f"**Execution status**: {'Success' if result2['success'] else 'Failed'}\n\n")
            f.write(f"**Total time**: {result2['total_time']:.2f} seconds\n\n")
            f.write("### Generated Plan\n\n")
            f.write(f"**Task analysis**: {result2['plan'].analysis}\n\n")
            f.write("**Selected tools**:\n\n")
            for i, tool_sel in enumerate(result2['plan'].selected_tools, 1):
                f.write(f"{i}. **{tool_sel.tool_name}**\n")
                f.write(f"   - Reason: {tool_sel.reason}\n")
                f.write(f"   - Parameters: {tool_sel.parameters}\n\n")
            f.write("### Execution Results\n\n")
            for i, step_result in enumerate(result2['execution']['execution_steps'], 1):
                f.write(f"#### Step {i}: {step_result['tool_name']}\n\n")
                f.write(f"**Status**: {'✅ Success' if step_result['success'] else '❌ Failed'}\n\n")
                if step_result.get('result'):
                    result_str = str(step_result['result'])
                    result_length = step_result.get('result_length', len(result_str))
                    f.write(f"**Result length**: {result_length} characters\n\n")
                    # Save full result
                    f.write(f"**Full result**:\n\n")
                    f.write(f"```\n{result_str}\n```\n\n")
            
            # Save Critic evaluation results
            if result2.get('evaluation'):
                eval = result2['evaluation']
                f.write("### 🔍 Critic Evaluation Results\n\n")
                f.write(f"- **Quality score**: {eval.quality_score:.2f}/1.0\n")
                f.write(f"- **Completeness score**: {eval.completeness_score:.2f}/1.0\n")
                f.write(f"- **Correctness score**: {eval.correctness_score:.2f}/1.0\n")
                f.write(f"- **Meets requirements**: {'✅ Yes' if eval.meets_requirements else '❌ No'}\n\n")
                
                if eval.strengths:
                    f.write(f"#### ✅ Strengths\n\n")
                    for strength in eval.strengths:
                        f.write(f"- {strength}\n")
                    f.write("\n")
                
                if eval.weaknesses:
                    f.write(f"#### ⚠️ Weaknesses\n\n")
                    for weakness in eval.weaknesses:
                        f.write(f"- {weakness}\n")
                    f.write("\n")
                
                if eval.improvements:
                    f.write(f"#### 💡 Improvement Suggestions\n\n")
                    for improvement in eval.improvements:
                        f.write(f"- {improvement}\n")
                    f.write("\n")
                
                if eval.overall_feedback:
                    f.write(f"#### 📝 Overall Feedback\n\n")
                    f.write(f"{eval.overall_feedback}\n\n")
                
                if eval.needs_reexecution:
                    f.write(f"#### 🔄 Re-execution Suggestion\n\n")
                    f.write(f"- **Re-execution needed**: Yes\n")
                    if eval.reexecution_reason:
                        f.write(f"- **Reason**: {eval.reexecution_reason}\n")
                    f.write("\n")
            
            if result2.get('reexecution_count', 0) > 0:
                f.write(f"### 🔄 Re-execution\n\n")
                f.write(f"- **Re-execution count**: {result2['reexecution_count']}\n\n")
    
    # Also save results in JSON format
    import json
    json_file = output_dir / "planner_worker_full_demo_result.json"
    
    # Convert results to JSON-serializable format
    json_result = {
        "execution_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "tests": {}
    }
    
    if result1:
        json_result["tests"]["evolution_map"] = {
            "success": result1['success'],
            "total_time": result1['total_time'],
            "plan": {
                "analysis": result1['plan'].analysis,
                "selected_tools": [
                    {
                        "tool_name": tool.tool_name,
                        "reason": tool.reason,
                        "parameters": tool.parameters,
                        "order": tool.order
                    }
                    for tool in result1['plan'].selected_tools
                ],
                "steps": result1['plan'].steps,
                "expected_output": result1['plan'].expected_output
            },
            "executions": [
                {
                    "execution_steps": exec_data['execution_steps'],
                    "success": exec_data['success'],
                    "errors": exec_data.get('errors', [])
                }
                for exec_data in result1.get('all_executions', [result1['execution']])
            ],
            "evaluation": {
                "quality_score": result1['evaluation'].quality_score if result1.get('evaluation') else None,
                "completeness_score": result1['evaluation'].completeness_score if result1.get('evaluation') else None,
                "correctness_score": result1['evaluation'].correctness_score if result1.get('evaluation') else None,
                "meets_requirements": result1['evaluation'].meets_requirements if result1.get('evaluation') else None,
                "strengths": result1['evaluation'].strengths if result1.get('evaluation') else [],
                "weaknesses": result1['evaluation'].weaknesses if result1.get('evaluation') else [],
                "improvements": result1['evaluation'].improvements if result1.get('evaluation') else [],
                "overall_feedback": result1['evaluation'].overall_feedback if result1.get('evaluation') else None
            } if result1.get('evaluation') else None,
            "reexecution_count": result1.get('reexecution_count', 0)
        }
    
    if result2:
        json_result["tests"]["code_extractor"] = {
            "success": result2['success'],
            "total_time": result2['total_time'],
            "plan": {
                "analysis": result2['plan'].analysis,
                "selected_tools": [
                    {
                        "tool_name": tool.tool_name,
                        "reason": tool.reason,
                        "parameters": tool.parameters,
                        "order": tool.order
                    }
                    for tool in result2['plan'].selected_tools
                ],
                "steps": result2['plan'].steps,
                "expected_output": result2['plan'].expected_output
            },
            "executions": [
                {
                    "execution_steps": exec_data['execution_steps'],
                    "success": exec_data['success'],
                    "errors": exec_data.get('errors', [])
                }
                for exec_data in result2.get('all_executions', [result2['execution']])
            ],
            "evaluation": {
                "quality_score": result2['evaluation'].quality_score if result2.get('evaluation') else None,
                "completeness_score": result2['evaluation'].completeness_score if result2.get('evaluation') else None,
                "correctness_score": result2['evaluation'].correctness_score if result2.get('evaluation') else None,
                "meets_requirements": result2['evaluation'].meets_requirements if result2.get('evaluation') else None,
                "strengths": result2['evaluation'].strengths if result2.get('evaluation') else [],
                "weaknesses": result2['evaluation'].weaknesses if result2.get('evaluation') else [],
                "improvements": result2['evaluation'].improvements if result2.get('evaluation') else [],
                "overall_feedback": result2['evaluation'].overall_feedback if result2.get('evaluation') else None
            } if result2.get('evaluation') else None,
            "reexecution_count": result2.get('reexecution_count', 0)
        }
    
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(json_result, f, ensure_ascii=False, indent=2)
    
    print()
    print("=" * 80)
    print(f"💾 Full results saved to:")
    print(f"   - Markdown: {result_file}")
    print(f"   - JSON: {json_file}")
    print("=" * 80)
    
    # Summary
    print()
    print("=" * 80)
    print("📊 Test Summary")
    print("=" * 80)
    print()
    if result1:
        print(f"✅ Evolution Map: {'Success' if result1['success'] else 'Failed'} ({result1['total_time']:.2f}s)")
    if result2:
        print(f"✅ Code Extractor: {'Success' if result2['success'] else 'Failed'} ({result2['total_time']:.2f}s)")


if __name__ == "__main__":
    # Set API key (if environment variable is not set)
    if not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = "AIzaSyCvJE0q0EgejGg1nro3ZIBNehcHM01VwV0"
        os.environ["OPENAI_BASE_URL"] = "https://generativelanguage.googleapis.com/v1beta/openai"
    
    main()

