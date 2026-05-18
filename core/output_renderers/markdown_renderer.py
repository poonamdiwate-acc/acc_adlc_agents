"""Markdown output renderer for agent results."""

from __future__ import annotations

from typing import Any, Dict

from core.output_renderers.base import OutputRenderer, RenderedOutput


class MarkdownRenderer(OutputRenderer):
    """Render agent output as Markdown."""

    @property
    def format_name(self) -> str:
        return "md"

    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        """Render result as Markdown.

        Args:
            result: The agent's structured output
            agent_id: Agent identifier
            run_id: Run identifier

        Returns:
            RenderedOutput with markdown bytes, content-type, and filename.
        """
        lines = []
        
        # Header
        lines.append(f"# {agent_id} - Technology Stack Recommendations")
        lines.append(f"\n**Run ID:** `{run_id}`\n")
        
        # Extract recommendations
        recommendations = result.get("tech_stack_recommendations", [])
        summary = result.get("tech_stack_summary", {})
        
        # Summary section
        if summary:
            lines.append("## Executive Summary\n")
            lines.append(f"- **Total Stacks Evaluated:** {summary.get('total_stacks_evaluated', 0)}")
            lines.append(f"- **Recommended Stack:** {summary.get('recommended_stack', 'N/A')}")
            lines.append(f"- **Overall Confidence:** {summary.get('overall_confidence', 0.0):.0%}")
            lines.append(f"- **Decision:** `{summary.get('recommendation', 'N/A')}`")
            
            comparison = summary.get("comparison_summary", "")
            if comparison:
                lines.append(f"\n{comparison}\n")
        
        # Recommendations section
        if recommendations:
            lines.append("\n---\n")
            lines.append("## Technology Stack Recommendations\n")
            
            for rec in recommendations:
                rec_id = rec.get("recommendation_id", "")
                stack_name = rec.get("stack_name", "Unknown Stack")
                confidence = rec.get("confidence_score", 0.0)
                
                lines.append(f"### {rec_id}: {stack_name}")
                lines.append(f"\n**Confidence Score:** {confidence:.0%}\n")
                
                # Stack components
                components = rec.get("stack_components", {})
                if components:
                    lines.append("#### Stack Components\n")
                    for key, value in components.items():
                        label = key.replace("_", " ").title()
                        lines.append(f"- **{label}:** {value}")
                    lines.append("")
                
                # Cloud deployment
                cloud = rec.get("cloud_deployment", {})
                if cloud:
                    lines.append("#### Cloud Deployment\n")
                    primary = cloud.get("primary_platform", "N/A")
                    lines.append(f"- **Primary Platform:** {primary}")
                    
                    multi = cloud.get("multi_cloud_support", False)
                    lines.append(f"- **Multi-Cloud Support:** {'Yes' if multi else 'No'}")
                    
                    services = cloud.get("managed_services", [])
                    if services:
                        lines.append(f"- **Managed Services:** {', '.join(services)}")
                    lines.append("")
                
                # Pros
                pros = rec.get("pros", [])
                if pros:
                    lines.append("#### Advantages\n")
                    for pro in pros:
                        lines.append(f"✅ {pro}")
                    lines.append("")
                
                # Cons
                cons = rec.get("cons", [])
                if cons:
                    lines.append("#### Considerations\n")
                    for con in cons:
                        lines.append(f"⚠️ {con}")
                    lines.append("")
                
                # Cost estimate
                cost = rec.get("cost_estimate", {})
                if cost:
                    lines.append("#### Cost Estimate\n")
                    monthly = cost.get("monthly_estimate", "N/A")
                    lines.append(f"- **Monthly Estimate:** {monthly}")
                    
                    breakdown = cost.get("cost_breakdown", {})
                    if breakdown:
                        lines.append("- **Breakdown:**")
                        for item, amount in breakdown.items():
                            lines.append(f"  - {item}: {amount}")
                    lines.append("")
                
                # Requirements coverage
                req_refs = rec.get("req_id_refs", [])
                if req_refs:
                    lines.append(f"#### Requirements Coverage\n")
                    lines.append(f"Addresses {len(req_refs)} requirements: {', '.join(req_refs[:10])}")
                    if len(req_refs) > 10:
                        lines.append(f" ... and {len(req_refs) - 10} more")
                    lines.append("")
                
                lines.append("\n---\n")
        
        # Decision factors
        decision_factors = summary.get("decision_factors", [])
        if decision_factors:
            lines.append("## Key Decision Factors\n")
            for i, factor in enumerate(decision_factors, 1):
                lines.append(f"{i}. {factor}")
            lines.append("")
        
        # Footer
        lines.append("\n---\n")
        lines.append(f"*Generated by ADLC Agent {agent_id} | Run: {run_id}*")
        
        markdown_content = "\n".join(lines)
        
        return RenderedOutput(
            content=markdown_content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
            filename=f"{agent_id}_{run_id}.md",
        )
