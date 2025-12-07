#!/usr/bin/env python3
"""
Planner-Worker Architecture for PaperArena Agent

This module implements a planner-worker architecture where:
- Planner: Analyzes the task, generates a plan, and selects which tools to use
- Worker: Executes the plan by calling the selected tools

The Agent can autonomously think about which tools to use to achieve the goal.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from loguru import logger
from smolagents import CodeAgent, Tool
from smolagents.models import ChatMessage, MessageRole, Model
from smolagents.memory import ActionStep, PlanningStep, TaskStep, Timing, TokenUsage

from dotenv import load_dotenv
load_dotenv()


@dataclass
class ToolSelection:
    """Represents a tool selection in the plan"""
    tool_name: str
    reason: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    order: int = 0


@dataclass
class ExecutionPlan:
    """Represents a complete execution plan"""
    task: str
    analysis: str
    selected_tools: List[ToolSelection]
    steps: List[str]
    expected_output: str
    created_at: float = field(default_factory=time.time)


@dataclass
class CriticEvaluation:
    """Represents a critic evaluation of execution results"""
    quality_score: float  # 0.0 to 1.0
    completeness_score: float  # 0.0 to 1.0
    correctness_score: float  # 0.0 to 1.0
    meets_requirements: bool
    strengths: List[str]
    weaknesses: List[str]
    improvements: List[str]
    needs_reexecution: bool
    reexecution_reason: Optional[str] = None
    overall_feedback: str = ""


class Planner:
    """
    Planner component: Analyzes tasks and generates execution plans
    
    The Planner:
    1. Analyzes the task to understand what needs to be done
    2. Identifies which tools are needed
    3. Generates a step-by-step plan
    4. Selects the appropriate tools for each step
    """
    
    def __init__(
        self,
        model: Model,
        available_tools: List[Tool],
        planning_prompt_template: Optional[str] = None
    ):
        """
        Initialize the Planner
        
        Args:
            model: LLM model for planning
            available_tools: List of available tools
            planning_prompt_template: Custom planning prompt template
        """
        self.model = model
        self.available_tools = available_tools
        self.tool_descriptions = self._build_tool_descriptions()
        self.planning_prompt_template = planning_prompt_template or self._default_planning_prompt()
        
        logger.info(f"✅ Planner initialized with {len(available_tools)} tools")
    
    def _build_tool_descriptions(self) -> str:
        """Build a description of all available tools"""
        descriptions = []
        for tool in self.available_tools:
            desc = f"- **{tool.name}**: {tool.description}"
            if hasattr(tool, 'inputs') and tool.inputs:
                inputs_desc = ", ".join([f"{k}: {v.get('description', '')}" for k, v in tool.inputs.items()])
                desc += f"\n  - Inputs: {inputs_desc}"
            descriptions.append(desc)
        return "\n".join(descriptions)
    
    def _default_planning_prompt(self) -> str:
        """Default planning prompt template"""
        return """You are a task planner for a research paper analysis agent. Your job is to analyze the task and create a detailed execution plan.

Available Tools:
{tool_descriptions}

Task: {task}

Please analyze the task and create a detailed plan. Your response should be a JSON object with the following structure:
{{
    "analysis": "Detailed analysis of what needs to be done",
    "selected_tools": [
        {{
            "tool_name": "name_of_tool",
            "reason": "Why this tool is needed",
            "parameters": {{"param1": "value1"}},
            "order": 1
        }}
    ],
    "steps": [
        "Step 1: ...",
        "Step 2: ..."
    ],
    "expected_output": "What the final output should look like"
}}

Important:
1. Only select tools that are actually needed for the task
2. Order the tools logically (what needs to be done first)
3. Provide clear reasons for each tool selection
4. Make sure the plan is executable"""
    
    def plan(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None
    ) -> ExecutionPlan:
        """
        Generate an execution plan for the given task
        
        Args:
            task: The task to plan for
            context: Optional context information
            
        Returns:
            ExecutionPlan: The generated execution plan
        """
        logger.info("🧠 Planner: Analyzing task and generating plan...")
        
        # Build planning prompt
        prompt = self.planning_prompt_template.format(
            tool_descriptions=self.tool_descriptions,
            task=task
        )
        
        # Add context if provided
        if context:
            # Convert context to JSON-serializable format
            serializable_context = {}
            for key, value in context.items():
                if isinstance(value, ExecutionPlan):
                    # Convert ExecutionPlan to dict
                    serializable_context[key] = {
                        "task": value.task,
                        "analysis": value.analysis,
                        "selected_tools": [
                            {
                                "tool_name": tool.tool_name,
                                "reason": tool.reason,
                                "parameters": tool.parameters,
                                "order": tool.order
                            }
                            for tool in value.selected_tools
                        ],
                        "steps": value.steps,
                        "expected_output": value.expected_output
                    }
                elif isinstance(value, CriticEvaluation):
                    # Convert CriticEvaluation to dict
                    serializable_context[key] = {
                        "quality_score": value.quality_score,
                        "completeness_score": value.completeness_score,
                        "correctness_score": value.correctness_score,
                        "meets_requirements": value.meets_requirements,
                        "strengths": value.strengths,
                        "weaknesses": value.weaknesses,
                        "improvements": value.improvements,
                        "needs_reexecution": value.needs_reexecution,
                        "reexecution_reason": value.reexecution_reason,
                        "overall_feedback": value.overall_feedback
                    }
                elif isinstance(value, (dict, list, str, int, float, bool, type(None))):
                    # Already JSON-serializable
                    serializable_context[key] = value
                else:
                    # Convert other objects to string
                    serializable_context[key] = str(value)
            
            context_str = json.dumps(serializable_context, indent=2)
            prompt += f"\n\nAdditional Context:\n{context_str}"
        
        # Call LLM to generate plan
        messages = [
            ChatMessage(role=MessageRole.USER, content=[{"type": "text", "text": prompt}])
        ]
        
        start_time = time.time()
        response = self.model.generate(messages)
        planning_time = time.time() - start_time
        
        # Parse the response
        plan_text = response.content[0]["text"] if isinstance(response.content, list) else str(response.content)
        
        # Try to extract JSON from the response
        plan_data = self._parse_plan_response(plan_text)
        
        # Build ExecutionPlan
        execution_plan = ExecutionPlan(
            task=task,
            analysis=plan_data.get("analysis", ""),
            selected_tools=[
                ToolSelection(**tool_data) for tool_data in plan_data.get("selected_tools", [])
            ],
            steps=plan_data.get("steps", []),
            expected_output=plan_data.get("expected_output", "")
        )
        
        logger.info(f"✅ Planner: Generated plan with {len(execution_plan.selected_tools)} tools")
        logger.info(f"   Planning time: {planning_time:.2f}s")
        
        return execution_plan
    
    def _parse_plan_response(self, response_text: str) -> Dict[str, Any]:
        """Parse the LLM response to extract plan data"""
        try:
            # Try to find JSON in the response
            # Look for JSON code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find JSON object directly
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = response_text
            
            plan_data = json.loads(json_str)
            return plan_data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Failed to parse plan as JSON: {e}")
            logger.warning(f"   Response: {response_text[:500]}")
            # Return a fallback plan
            return {
                "analysis": "Failed to parse plan. Using fallback.",
                "selected_tools": [],
                "steps": ["Execute the task using available tools"],
                "expected_output": "Task completion"
            }


class Worker:
    """
    Worker component: Executes the plan by calling tools
    
    The Worker:
    1. Receives an execution plan
    2. Executes each step in order
    3. Calls the selected tools with appropriate parameters
    4. Collects results and handles errors
    """
    
    def __init__(
        self,
        tools: Dict[str, Tool],
        max_steps: int = 20
    ):
        """
        Initialize the Worker
        
        Args:
            tools: Dictionary of available tools (name -> Tool)
            max_steps: Maximum number of execution steps
        """
        self.tools = tools
        self.max_steps = max_steps
        self.execution_history: List[Dict[str, Any]] = []
        
        logger.info(f"✅ Worker initialized with {len(tools)} tools")
    
    def execute_plan(
        self,
        plan: ExecutionPlan,
        agent: Optional[CodeAgent] = None
    ) -> Dict[str, Any]:
        """
        Execute the given execution plan
        
        Args:
            plan: The execution plan to execute
            agent: Optional CodeAgent for tool execution
            
        Returns:
            Dict with execution results
        """
        logger.info("🔧 Worker: Starting plan execution...")
        logger.info(f"   Plan has {len(plan.selected_tools)} tool selections")
        
        results = {
            "plan": plan,
            "execution_steps": [],
            "final_result": None,
            "success": False,
            "errors": []
        }
        
        # Sort tools by order
        sorted_tools = sorted(plan.selected_tools, key=lambda t: t.order)
        
        # Execute each tool in order
        for i, tool_selection in enumerate(sorted_tools, 1):
            logger.info(f"   Step {i}/{len(sorted_tools)}: Executing {tool_selection.tool_name}")
            
            step_result = {
                "step": i,
                "tool_name": tool_selection.tool_name,
                "reason": tool_selection.reason,
                "parameters": tool_selection.parameters,
                "result": None,
                "error": None,
                "success": False
            }
            
            try:
                # Get the tool
                if tool_selection.tool_name not in self.tools:
                    error_msg = f"Tool '{tool_selection.tool_name}' not found"
                    logger.error(f"   ❌ {error_msg}")
                    step_result["error"] = error_msg
                    results["errors"].append(error_msg)
                    continue
                
                tool = self.tools[tool_selection.tool_name]
                
                # Execute the tool directly
                # Tool.forward() expects keyword arguments based on tool.inputs
                # Convert parameters dict to keyword arguments
                if isinstance(tool_selection.parameters, dict):
                    # Validate that all required parameters are present
                    from smolagents.tools import validate_tool_arguments
                    try:
                        validate_tool_arguments(tool, tool_selection.parameters)
                        result = tool.forward(**tool_selection.parameters)
                    except Exception as e:
                        # If validation fails, try with the parameters as-is
                        logger.warning(f"   ⚠️ Parameter validation failed: {e}, trying direct call")
                        result = tool.forward(**tool_selection.parameters)
                else:
                    # If parameters is not a dict, try to call with it directly
                    result = tool.forward(tool_selection.parameters)
                
                # Save full result (don't truncate)
                step_result["result"] = str(result)
                step_result["result_length"] = len(str(result))
                step_result["success"] = True
                logger.info(f"   ✅ Tool executed successfully")
                logger.info(f"   📊 Result length: {len(str(result))} characters")
                
            except Exception as e:
                error_msg = f"Error executing {tool_selection.tool_name}: {str(e)}"
                logger.error(f"   ❌ {error_msg}")
                step_result["error"] = error_msg
                results["errors"].append(error_msg)
            
            results["execution_steps"].append(step_result)
        
        # Determine overall success
        results["success"] = len(results["errors"]) == 0
        
        if results["success"]:
            logger.info("✅ Worker: Plan execution completed successfully")
        else:
            logger.warning(f"⚠️ Worker: Plan execution completed with {len(results['errors'])} errors")
        
        return results


class Critic:
    """
    Critic component: Evaluates execution results and provides feedback
    
    The Critic:
    1. Evaluates the quality of execution results
    2. Checks if the task requirements are met
    3. Identifies strengths and weaknesses
    4. Provides improvement suggestions
    5. Decides if re-execution is needed
    """
    
    def __init__(
        self,
        model: Model,
        evaluation_prompt_template: Optional[str] = None
    ):
        """
        Initialize the Critic
        
        Args:
            model: LLM model for evaluation
            evaluation_prompt_template: Custom evaluation prompt template
        """
        self.model = model
        self.evaluation_prompt_template = evaluation_prompt_template or self._default_evaluation_prompt()
        
        logger.info("✅ Critic initialized")
    
    def _default_evaluation_prompt(self) -> str:
        """Default evaluation prompt template"""
        return """You are a quality critic for a research paper analysis agent. Your job is to evaluate the execution results and provide feedback.

Task: {task}

Original Plan:
{plan_summary}

Execution Results:
{execution_results}

Please evaluate the execution results and provide a comprehensive assessment. Your response should be a JSON object with the following structure:
{{
    "quality_score": 0.0-1.0,  // Overall quality score (0.0 = poor, 1.0 = excellent)
    "completeness_score": 0.0-1.0,  // How complete the results are (0.0 = incomplete, 1.0 = complete)
    "correctness_score": 0.0-1.0,  // How correct the results are (0.0 = incorrect, 1.0 = correct)
    "meets_requirements": true/false,  // Whether the results meet the task requirements
    "strengths": [
        "Strength 1",
        "Strength 2"
    ],
    "weaknesses": [
        "Weakness 1",
        "Weakness 2"
    ],
    "improvements": [
        "Improvement suggestion 1",
        "Improvement suggestion 2"
    ],
    "needs_reexecution": true/false,  // Whether the task needs to be re-executed
    "reexecution_reason": "Reason if needs_reexecution is true",
    "overall_feedback": "Overall feedback and summary"
}}

Important:
1. Be objective and thorough in your evaluation
2. Consider both the execution process and the final results
3. Provide actionable improvement suggestions
4. Only recommend re-execution if there are critical issues that cannot be fixed otherwise"""
    
    def evaluate(
        self,
        task: str,
        plan: ExecutionPlan,
        execution_results: Dict[str, Any]
    ) -> CriticEvaluation:
        """
        Evaluate execution results
        
        Args:
            task: The original task
            plan: The execution plan
            execution_results: The execution results
            
        Returns:
            CriticEvaluation: The evaluation results
        """
        logger.info("🔍 Critic: Evaluating execution results...")
        
        # Build evaluation prompt
        plan_summary = self._format_plan_summary(plan)
        execution_summary = self._format_execution_summary(execution_results)
        
        prompt = self.evaluation_prompt_template.format(
            task=task,
            plan_summary=plan_summary,
            execution_results=execution_summary
        )
        
        # Call LLM to evaluate
        messages = [
            ChatMessage(role=MessageRole.USER, content=[{"type": "text", "text": prompt}])
        ]
        
        start_time = time.time()
        response = self.model.generate(messages)
        evaluation_time = time.time() - start_time
        
        # Parse the response
        eval_text = response.content[0]["text"] if isinstance(response.content, list) else str(response.content)
        
        # Try to extract JSON from the response
        eval_data = self._parse_evaluation_response(eval_text)
        
        # Build CriticEvaluation
        evaluation = CriticEvaluation(
            quality_score=float(eval_data.get("quality_score", 0.5)),
            completeness_score=float(eval_data.get("completeness_score", 0.5)),
            correctness_score=float(eval_data.get("correctness_score", 0.5)),
            meets_requirements=bool(eval_data.get("meets_requirements", False)),
            strengths=eval_data.get("strengths", []),
            weaknesses=eval_data.get("weaknesses", []),
            improvements=eval_data.get("improvements", []),
            needs_reexecution=bool(eval_data.get("needs_reexecution", False)),
            reexecution_reason=eval_data.get("reexecution_reason"),
            overall_feedback=eval_data.get("overall_feedback", "")
        )
        
        logger.info(f"✅ Critic: Evaluation completed")
        logger.info(f"   Quality: {evaluation.quality_score:.2f}/1.0")
        logger.info(f"   Completeness: {evaluation.completeness_score:.2f}/1.0")
        logger.info(f"   Correctness: {evaluation.correctness_score:.2f}/1.0")
        logger.info(f"   Meets requirements: {evaluation.meets_requirements}")
        logger.info(f"   Needs re-execution: {evaluation.needs_reexecution}")
        logger.info(f"   Evaluation time: {evaluation_time:.2f}s")
        
        return evaluation
    
    def _format_plan_summary(self, plan: ExecutionPlan) -> str:
        """Format plan for evaluation prompt"""
        summary = f"Task Analysis: {plan.analysis}\n\n"
        summary += f"Selected Tools ({len(plan.selected_tools)}):\n"
        for tool_sel in sorted(plan.selected_tools, key=lambda t: t.order):
            summary += f"  {tool_sel.order}. {tool_sel.tool_name}: {tool_sel.reason}\n"
        summary += f"\nSteps ({len(plan.steps)}):\n"
        for i, step in enumerate(plan.steps, 1):
            summary += f"  {i}. {step}\n"
        summary += f"\nExpected Output: {plan.expected_output}"
        return summary
    
    def _format_execution_summary(self, execution_results: Dict[str, Any]) -> str:
        """Format execution results for evaluation prompt"""
        summary = f"Execution Status: {'✅ Success' if execution_results.get('success') else '❌ Failed'}\n\n"
        
        if execution_results.get('errors'):
            summary += f"Errors ({len(execution_results['errors'])}):\n"
            for error in execution_results['errors']:
                summary += f"  - {error}\n"
            summary += "\n"
        
        summary += f"Execution Steps ({len(execution_results.get('execution_steps', []))}):\n"
        for i, step in enumerate(execution_results.get('execution_steps', []), 1):
            step_summary = f"  Step {i}: {step.get('tool_name', 'Unknown')}\n"
            step_summary += f"    Status: {'✅ Success' if step.get('success') else '❌ Failed'}\n"
            if step.get('error'):
                step_summary += f"    Error: {step['error']}\n"
            if step.get('result'):
                result_str = str(step['result'])
                # Truncate long results for evaluation
                if len(result_str) > 1000:
                    result_str = result_str[:1000] + f"... (truncated, total {len(result_str)} chars)"
                step_summary += f"    Result: {result_str}\n"
            summary += step_summary + "\n"
        
        return summary
    
    def _parse_evaluation_response(self, response_text: str) -> Dict[str, Any]:
        """Parse the LLM response to extract evaluation data"""
        try:
            # Try to find JSON in the response
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find JSON object directly
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = response_text
            
            eval_data = json.loads(json_str)
            return eval_data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Failed to parse evaluation as JSON: {e}")
            logger.warning(f"   Response: {response_text[:500]}")
            # Return a fallback evaluation
            return {
                "quality_score": 0.5,
                "completeness_score": 0.5,
                "correctness_score": 0.5,
                "meets_requirements": False,
                "strengths": [],
                "weaknesses": ["Failed to parse evaluation response"],
                "improvements": ["Fix evaluation parsing"],
                "needs_reexecution": False,
                "overall_feedback": "Evaluation parsing failed"
            }


class PlannerWorkerAgent:
    """
    Planner-Worker Agent: Combines Planner and Worker for autonomous task execution
    
    This agent:
    1. Uses Planner to analyze task and generate plan
    2. Uses Worker to execute the plan
    3. Can reflect on results and adjust plan if needed
    """
    
    def __init__(
        self,
        model: Model,
        tools: List[Tool],
        max_steps: int = 20,
        enable_reflection: bool = True,
        enable_critic: bool = True,
        max_reexecutions: int = 2
    ):
        """
        Initialize the Planner-Worker Agent
        
        Args:
            model: LLM model for planning and execution
            tools: List of available tools
            max_steps: Maximum execution steps
            enable_reflection: Whether to enable reflection on results
            enable_critic: Whether to enable critic evaluation
            max_reexecutions: Maximum number of re-executions allowed
        """
        self.model = model
        self.tools = tools
        self.tools_dict = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.enable_reflection = enable_reflection
        self.enable_critic = enable_critic
        self.max_reexecutions = max_reexecutions
        
        # Initialize Planner, Worker, and Critic
        self.planner = Planner(model=model, available_tools=tools)
        self.worker = Worker(tools=self.tools_dict, max_steps=max_steps)
        if enable_critic:
            self.critic = Critic(model=model)
        else:
            self.critic = None
        
        # Create a CodeAgent for tool execution
        self.execution_agent = CodeAgent(
            tools=tools,
            model=model,
            max_steps=max_steps,
            verbosity_level=1
        )
        
        logger.info("✅ PlannerWorkerAgent initialized")
        logger.info(f"   Tools: {len(tools)}")
        logger.info(f"   Max steps: {max_steps}")
        logger.info(f"   Reflection: {enable_reflection}")
        logger.info(f"   Critic: {enable_critic}")
        logger.info(f"   Max re-executions: {max_reexecutions}")
    
    def run(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a task using planner-worker architecture
        
        Args:
            task: The task to execute
            context: Optional context information
            
        Returns:
            Dict with execution results
        """
        logger.info("=" * 80)
        logger.info("🚀 PlannerWorkerAgent: Starting task execution")
        logger.info("=" * 80)
        logger.info(f"📝 Task: {task}")
        
        start_time = time.time()
        
        # Step 1: Planning
        logger.info("\n" + "=" * 80)
        logger.info("📋 Phase 1: Planning")
        logger.info("=" * 80)
        
        plan = self.planner.plan(task=task, context=context)
        
        # Log the plan
        logger.info("\n📊 Generated Plan:")
        logger.info(f"   Analysis: {plan.analysis[:200]}...")
        logger.info(f"   Selected Tools: {len(plan.selected_tools)}")
        for tool_sel in plan.selected_tools:
            logger.info(f"     - {tool_sel.tool_name} (order: {tool_sel.order}): {tool_sel.reason}")
        logger.info(f"   Steps: {len(plan.steps)}")
        for i, step in enumerate(plan.steps, 1):
            logger.info(f"     {i}. {step}")
        
        # Step 2: Execution
        logger.info("\n" + "=" * 80)
        logger.info("🔧 Phase 2: Execution")
        logger.info("=" * 80)
        
        execution_results = self.worker.execute_plan(
            plan=plan,
            agent=self.execution_agent
        )
        
        # Store all execution attempts
        all_executions = [execution_results]
        
        # Step 3: Critic Evaluation (if enabled)
        evaluation = None
        reexecution_count = 0
        
        if self.enable_critic and self.critic:
            logger.info("\n" + "=" * 80)
            logger.info("🔍 Phase 3: Critic Evaluation")
            logger.info("=" * 80)
            
            evaluation = self.critic.evaluate(
                task=task,
                plan=plan,
                execution_results=execution_results
            )
            
            # Log evaluation details
            logger.info("\n📊 Evaluation Details:")
            logger.info(f"   Quality Score: {evaluation.quality_score:.2f}/1.0")
            logger.info(f"   Completeness Score: {evaluation.completeness_score:.2f}/1.0")
            logger.info(f"   Correctness Score: {evaluation.correctness_score:.2f}/1.0")
            logger.info(f"   Meets Requirements: {evaluation.meets_requirements}")
            
            if evaluation.strengths:
                logger.info(f"\n✅ Strengths ({len(evaluation.strengths)}):")
                for strength in evaluation.strengths:
                    logger.info(f"   - {strength}")
            
            if evaluation.weaknesses:
                logger.info(f"\n⚠️ Weaknesses ({len(evaluation.weaknesses)}):")
                for weakness in evaluation.weaknesses:
                    logger.info(f"   - {weakness}")
            
            if evaluation.improvements:
                logger.info(f"\n💡 Improvements ({len(evaluation.improvements)}):")
                for improvement in evaluation.improvements:
                    logger.info(f"   - {improvement}")
            
            if evaluation.overall_feedback:
                logger.info(f"\n📝 Overall Feedback:")
                logger.info(f"   {evaluation.overall_feedback}")
            
            # Step 4: Re-execution (if needed and allowed)
            if evaluation.needs_reexecution and reexecution_count < self.max_reexecutions:
                logger.info("\n" + "=" * 80)
                logger.info("🔄 Phase 4: Re-execution")
                logger.info("=" * 80)
                logger.info(f"   Reason: {evaluation.reexecution_reason}")
                logger.info(f"   Re-execution {reexecution_count + 1}/{self.max_reexecutions}")
                
                # Update plan based on feedback (simple approach: regenerate plan)
                # In a more sophisticated implementation, we could refine the plan
                logger.info("   Regenerating plan with improvements...")
                improved_plan = self.planner.plan(task=task, context={
                    "previous_plan": plan,
                    "evaluation": evaluation,
                    "improvements": evaluation.improvements
                })
                
                # Re-execute with improved plan
                reexecution_results = self.worker.execute_plan(
                    plan=improved_plan,
                    agent=self.execution_agent
                )
                
                # Store re-execution results
                all_executions.append(reexecution_results)
                
                reexecution_count += 1
                
                # Re-evaluate if still enabled
                if self.enable_critic:
                    evaluation = self.critic.evaluate(
                        task=task,
                        plan=improved_plan,
                        execution_results=reexecution_results
                    )
                    logger.info(f"   Re-evaluation Quality: {evaluation.quality_score:.2f}/1.0")
                
                # Update execution_results to the latest
                execution_results = reexecution_results
        
        # Step 5: Reflection (optional, legacy)
        if self.enable_reflection and not execution_results["success"]:
            logger.info("\n" + "=" * 80)
            logger.info("🤔 Phase 5: Reflection (Legacy)")
            logger.info("=" * 80)
            logger.info("   Execution had errors, but reflection is not yet implemented")
        
        # Build final result
        total_time = time.time() - start_time
        
        result = {
            "task": task,
            "plan": plan,
            "execution": execution_results,  # Latest execution
            "all_executions": all_executions,  # All execution attempts
            "evaluation": evaluation,
            "reexecution_count": reexecution_count,
            "total_time": total_time,
            "success": execution_results["success"] and (evaluation.meets_requirements if evaluation else execution_results["success"])
        }
        
        logger.info("\n" + "=" * 80)
        logger.info("✅ PlannerWorkerAgent: Task execution completed")
        logger.info("=" * 80)
        logger.info(f"   Total time: {total_time:.2f}s")
        logger.info(f"   Success: {result['success']}")
        
        return result
    
    def get_plan_summary(self, plan: ExecutionPlan) -> str:
        """Get a human-readable summary of the plan"""
        summary = f"Plan for: {plan.task}\n\n"
        summary += f"Analysis: {plan.analysis}\n\n"
        summary += f"Selected Tools ({len(plan.selected_tools)}):\n"
        for tool_sel in sorted(plan.selected_tools, key=lambda t: t.order):
            summary += f"  {tool_sel.order}. {tool_sel.tool_name}: {tool_sel.reason}\n"
        summary += f"\nSteps ({len(plan.steps)}):\n"
        for i, step in enumerate(plan.steps, 1):
            summary += f"  {i}. {step}\n"
        return summary

