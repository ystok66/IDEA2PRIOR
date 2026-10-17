#!/usr/bin/env python3
"""
Planner-Worker Architecture for PaperArena Agent

This module implements a planner-worker architecture where:
- Planner: Analyzes the task, generates a plan, and selects which tools to use
- Worker: Executes the plan by calling the selected tools

The Agent can autonomously think about which tools to use to achieve the goal.
"""

import json
import math
import copy
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from pipelines.runtime import invoke_model

from loguru import logger
from smolagents import CodeAgent, Tool
from smolagents.models import ChatMessage, MessageRole, Model
from smolagents.memory import ActionStep, PlanningStep, TaskStep, Timing, TokenUsage

# API routes are supplied explicitly; importing this module never loads .env.


@dataclass
class ToolSelection:
    """Represents a tool selection in the plan"""
    tool_name: str
    reason: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    order: int = 0
    step_id: str = ""
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """Represents a complete execution plan"""
    task: str
    analysis: str
    selected_tools: List[ToolSelection]
    steps: List[str]
    expected_output: str
    created_at: float = field(default_factory=time.time)
    noop: bool = False


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
            "order": 1,
            "step_id": "step_1",
            "dependencies": []
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
4. Make sure the plan is executable
5. Assign stable step_id values and dependencies. Output references use {{"$ref": "step_id.field"}} and must name a declared dependency.
6. An empty selected_tools list is not success. Use an explicit top-level "noop": true only when the task legitimately requires no tool work."""
    
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
        
        start_time = time.monotonic()
        response = invoke_model(self.model.generate, messages, stage="planner.plan")
        planning_time = time.monotonic() - start_time
        
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
            expected_output=plan_data.get("expected_output", ""),
            noop=plan_data.get("noop") is True,
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
            if not isinstance(plan_data, dict) or not isinstance(plan_data.get("selected_tools"), list):
                raise ValueError("INVALID_OUTPUT: plan must contain a selected_tools list")
            if "noop" in plan_data and not isinstance(plan_data["noop"], bool):
                raise ValueError("INVALID_OUTPUT: noop must be a JSON boolean")
            for selection in plan_data["selected_tools"]:
                if not isinstance(selection, dict) or not isinstance(selection.get("tool_name"), str):
                    raise ValueError("INVALID_OUTPUT: tool selection must name a tool")
                if "dependencies" in selection and (not isinstance(selection["dependencies"], list) or not all(isinstance(dep, str) for dep in selection["dependencies"])):
                    raise ValueError("INVALID_OUTPUT: dependencies must be a string list")
                if "order" in selection and (isinstance(selection["order"], bool) or not isinstance(selection["order"], int)):
                    raise ValueError("INVALID_OUTPUT: order must be an integer")
            return plan_data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Failed to parse plan as JSON: {e}")
            logger.warning(f"   Response: {response_text[:500]}")
            raise ValueError("INVALID_OUTPUT: planner did not return valid JSON") from e


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
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 0:
            raise ValueError("max_steps must be a nonnegative integer")
        self.tools = tools
        self.max_steps = max_steps
        self.execution_history: List[Dict[str, Any]] = []
        
        logger.info(f"✅ Worker initialized with {len(tools)} tools")
    
    def execute_plan(
        self,
        plan: ExecutionPlan,
        agent: Optional[CodeAgent] = None,
        previous_results: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the given execution plan
        
        Args:
            plan: The execution plan to execute
            agent: Optional CodeAgent for tool execution
            
        Returns:
            Dict with execution results
        """
        results = {"plan": plan, "execution_steps": [], "final_result": None,
                   "success": False, "errors": [], "status": "NOT_RUN", "tool_attempts": 0}
        ordered = sorted(enumerate(plan.selected_tools), key=lambda pair: (pair[1].order, pair[0]))
        if not ordered:
            results.update(success=plan.noop is True, status="NOOP" if plan.noop is True else "INVALID_PLAN")
            if not results["success"]:
                results["errors"].append("Empty plan is not an explicit NOOP")
            return results
        ids = [selection.step_id or f"step-{original + 1}" for original, selection in ordered]
        if len(ids) != len(set(ids)):
            results["errors"].append("Duplicate step IDs")
            results["status"] = "INVALID_PLAN"
            return results
        previous = {row["step_id"]: row for row in (previous_results or {}).get("execution_steps", [])}
        completed = {}
        changed = set()
        for position, ((original, selection), step_id) in enumerate(zip(ordered, ids), 1):
            row = {"step": position, "step_id": step_id, "tool_name": selection.tool_name,
                   "reason": selection.reason, "parameters": copy.deepcopy(selection.parameters),
                   "dependencies": list(selection.dependencies), "result": None, "raw_result": None,
                   "error": None, "success": False, "status": "NOT_RUN", "attempted": False}
            results["execution_steps"].append(row)  # All branches keep their denominator.
            try:
                if not all(dep in completed and completed[dep]["success"] for dep in selection.dependencies):
                    row.update(status="BLOCKED", error="Missing, forward, or failed dependency")
                    continue
                if selection.tool_name not in self.tools:
                    row.update(status="FAILED", error=f"Unknown tool: {selection.tool_name}")
                    continue
                tool = self.tools[selection.tool_name]
                old = previous.get(step_id)
                if old and old.get("success") and old["tool_name"] == selection.tool_name and old["parameters"] == selection.parameters and old.get("dependencies", []) == selection.dependencies and not any(dep in changed for dep in selection.dependencies):
                    row.update(copy.deepcopy(old), recovered=True, attempted=False)
                    continue
                if old and old.get("attempted") and getattr(tool, "idempotent", False) is not True:
                    row.update(status="BLOCKED", error="Prior side-effect outcome cannot be safely replayed")
                    continue
                parameters = self._resolve_parameters(selection.parameters, completed, selection.dependencies)
                row["resolved_parameters"] = copy.deepcopy(parameters)
                from smolagents.tools import validate_tool_arguments
                try:
                    validate_tool_arguments(tool, parameters)
                except Exception as error:
                    row.update(status="INVALID_ARGUMENTS", error=str(error))
                    continue
                if results["tool_attempts"] >= self.max_steps:
                    row.update(status="NOT_RUN", error="Tool attempt budget exhausted")
                    continue
                results["tool_attempts"] += 1
                row["attempted"] = True
                changed.add(step_id)
                started = time.monotonic()
                try:
                    value = tool.forward(**parameters) if isinstance(parameters, dict) else tool.forward(parameters)
                except Exception as error:
                    row.update(status="FAILED" if getattr(tool, "idempotent", False) is True else "UNKNOWN", error=f"{type(error).__name__}: {error}")
                    continue
                finally:
                    row["execution_seconds"] = time.monotonic() - started
                row.update(raw_result=value, result=str(value), result_length=len(str(value)))
                # Only tools declaring an interpreter can classify normal returns.
                interpreter = getattr(tool, "interpret_execution_result", None)
                status = interpreter(value) if callable(interpreter) else "SUCCEEDED"
                if status not in ("SUCCEEDED", "FAILED", "UNKNOWN", "NOT_APPLICABLE"):
                    raise ValueError("Tool interpreter returned an invalid status")
                row.update(status=status, success=status in ("SUCCEEDED", "NOT_APPLICABLE"))
                if not row["success"]:
                    row["error"] = f"Tool-declared result status: {status}"
            except Exception as error:
                row.update(status="INVALID_ARGUMENTS" if not row["attempted"] else "UNKNOWN", error=str(error))
            finally:
                completed[step_id] = row
                self.execution_history.append(copy.deepcopy(row))
                if row["error"]:
                    results["errors"].append(f"{step_id}: {row['error']}")
        results["success"] = all(row["success"] for row in results["execution_steps"])
        results["status"] = "SUCCEEDED" if results["success"] else "INCOMPLETE"
        successful = [row for row in results["execution_steps"] if row["success"]]
        results["final_result"] = successful[-1]["raw_result"] if successful else None
        return results

    @staticmethod
    def _resolve_parameters(value, completed, dependencies):
        """Only exact {'$ref': 'step_id.field.0'} objects are output references."""
        if isinstance(value, dict) and set(value) == {"$ref"}:
            parts = value["$ref"].split(".")
            if parts[0] not in dependencies:
                raise ValueError("Output reference must name a declared dependency")
            result = completed[parts[0]]["raw_result"]
            for part in parts[1:]:
                result = result[int(part)] if isinstance(result, (list, tuple)) else result[part]
            return copy.deepcopy(result)
        if isinstance(value, dict):
            return {key: Worker._resolve_parameters(item, completed, dependencies) for key, item in value.items()}
        if isinstance(value, list):
            return [Worker._resolve_parameters(item, completed, dependencies) for item in value]
        return value


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
        
        start_time = time.monotonic()
        response = invoke_model(self.model.generate, messages, stage="planner.critic")
        evaluation_time = time.monotonic() - start_time
        
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
                if len(result_str) > 12000:
                    result_str = result_str[:10000] + "\n[Critic input excerpt omitted the middle; the saved tool output is complete. Do not infer output truncation from this excerpt.]\n" + result_str[-2000:]
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
            if not isinstance(eval_data, dict):
                raise ValueError("INVALID_OUTPUT: critic response must be an object")
            for name in ("quality_score", "completeness_score", "correctness_score"):
                score = eval_data.get(name)
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError(f"INVALID_OUTPUT: {name} must be finite within [0,1]")
            for name in ("meets_requirements", "needs_reexecution"):
                if not isinstance(eval_data.get(name), bool):
                    raise ValueError(f"INVALID_OUTPUT: {name} must be a JSON boolean")
            for name in ("strengths", "weaknesses", "improvements"):
                value = eval_data.get(name)
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ValueError(f"INVALID_OUTPUT: {name} must be a string list")
            return eval_data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Failed to parse evaluation as JSON: {e}")
            logger.warning(f"   Response: {response_text[:500]}")
            raise ValueError("INVALID_OUTPUT: critic did not return valid JSON") from e


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
        if isinstance(max_reexecutions, bool) or not isinstance(max_reexecutions, int) or max_reexecutions < 0:
            raise ValueError("max_reexecutions must be a nonnegative integer")
        self.max_reexecutions = max_reexecutions
        # Preserve the pre-existing single conditional retry, including config=2.
        # A true multi-retry loop needs an explicit changed budget/protocol.
        self.effective_max_reexecutions = min(max_reexecutions, 1)
        
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
    
    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute aligned rounds; no unknown side effect is automatically replayed."""
        started = time.monotonic()
        rounds = []
        all_executions = []
        plan = None
        evaluation = None
        execution = {"success": False, "status": "NOT_RUN", "execution_steps": [], "errors": []}
        errors = []
        effective_retries = min(self.max_reexecutions, 1)
        remaining_steps = self.max_steps
        original_budget = self.worker.max_steps
        try:
            for round_index in range(1 + effective_retries):
                round_record = {"round": round_index, "plan": None, "execution": None,
                                "evaluation": None, "status": "NOT_RUN"}
                rounds.append(round_record)
                try:
                    round_context = context if round_index == 0 else {
                        "previous_plan": plan, "evaluation": evaluation,
                        "improvements": evaluation.improvements,
                    }
                    plan = self.planner.plan(task=task, context=round_context)
                    round_record["plan"] = plan
                except Exception as error:
                    round_record.update(status="INVALID_PLAN", error=f"{type(error).__name__}: {error}")
                    errors.append(round_record["error"])
                    break
                self.worker.max_steps = remaining_steps
                execution = self.worker.execute_plan(plan, self.execution_agent,
                    previous_results=all_executions[-1] if all_executions else None)
                remaining_steps -= execution.get("tool_attempts", 0)
                all_executions.append(execution)
                round_record["execution"] = execution
                round_record["status"] = execution.get("status", "UNKNOWN")
                evaluation = None
                if self.enable_critic and self.critic:
                    try:
                        evaluation = self.critic.evaluate(task=task, plan=plan, execution_results=execution)
                        round_record["evaluation"] = evaluation
                    except Exception as error:
                        round_record.update(status="INVALID_OUTPUT", error=f"{type(error).__name__}: {error}")
                        errors.append(round_record["error"])
                        break
                if not evaluation or not evaluation.needs_reexecution:
                    break
                # Unknown side effects cannot be resolved by regenerating a plan.
                if any(step.get("status") == "UNKNOWN" for step in execution.get("execution_steps", [])):
                    round_record["reexecution_status"] = "BLOCKED_UNKNOWN_SIDE_EFFECT"
                    break
                if round_index >= effective_retries or remaining_steps <= 0:
                    round_record["reexecution_status"] = "NOT_RUN_BUDGET"
                    break
        finally:
            self.worker.max_steps = original_budget
        return {
            "task": task, "plan": plan, "execution": execution,
            "all_executions": all_executions, "rounds": rounds, "evaluation": evaluation,
            "reexecution_count": max(0, len(all_executions) - 1),
            "configured_max_reexecutions": self.max_reexecutions,
            "effective_max_reexecutions": effective_retries,
            "budget_policy": "legacy_single_reexecution_v1; total_tool_attempts_bounded",
            "remaining_tool_attempts": remaining_steps,
            "total_time": time.monotonic() - started, "errors": errors,
            "success": bool(execution.get("success") and not errors and
                            (evaluation.meets_requirements if evaluation else True)),
        }
    
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

