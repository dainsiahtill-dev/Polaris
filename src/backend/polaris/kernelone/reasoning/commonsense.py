"""Commonsense Reasoning Engine for Polaris.

Provides causal reasoning, counterfactual reasoning, and analogical reasoning capabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CausalLink:
    """A link in a causal graph."""

    cause: str
    effect: str
    strength: float = 1.0  # 0-1


@dataclass(frozen=True)
class CausalGraph:
    """Causal relationship graph."""

    nodes: tuple[str, ...]
    links: tuple[CausalLink, ...]
    root_causes: tuple[str, ...] = field(default_factory=tuple)
    leaf_effects: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CounterfactualResult:
    """Result of counterfactual reasoning."""

    original_scenario: str
    hypothetical_change: str
    predicted_outcome: str
    confidence: float
    reasoning_chain: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnalogyResult:
    """Result of analogical reasoning."""

    source: str
    target: str
    similarity_score: float
    mapped_properties: tuple[str, ...] = field(default_factory=tuple)
    inferred_properties: tuple[str, ...] = field(default_factory=tuple)


# Common causal relationship patterns
CAUSAL_PATTERNS: tuple[tuple[str, str, float], ...] = (
    # (cause_pattern, effect_pattern, strength)
    (r"\bcauses?\b", r"leads? to", 0.9),
    (r"\bcauses?\b", r"results? in", 0.9),
    (r"\bproduces?\b", r"creates?", 0.8),
    (r"\bincreases?\b", r"causes?", 0.85),
    (r"\bdecreases?\b", r"reduces?", 0.85),
    (r"\benables?\b", r"allows?", 0.75),
    (r"\bprevents?\b", r"stops?", 0.8),
    (r"\brequires?\b", r"needs?", 0.7),
    (r"\bdetermines?\b", r"controls?", 0.85),
    (r"\baffects?\b", r"influences?", 0.7),
)

# Inverse causal relationships
INVERSE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bnot\b\s+\1", r"\2"),  # negation flip
)

# Common causal chains for inference
KNOWN_CAUSAL_CHAINS: tuple[tuple[str, str, str], ...] = (
    # (cause, intermediate, effect)
    ("rain", "wet_ground", "slippery_roads"),
    ("fire", "heat", "burns"),
    ("exercise", "sweat", "dehydration"),
    ("cold", "ice", "slippery"),
    ("work_hard", "success", "recognition"),
    ("smoking", "cancer", "death"),
    ("rain", "umbrella", "dry"),
    ("study", "knowledge", "exam_pass"),
    ("hungry", "eat", "full"),
    ("tired", "sleep", "refreshed"),
)


@dataclass
class CommonsenseReasoner:
    """Commonsense reasoning engine."""

    async def causal_inference(self, observation: str) -> CausalGraph:
        """Infer causal relationships from an observation.

        Builds a causal graph showing cause-effect relationships.

        Args:
            observation: A text describing a situation or event.

        Returns:
            CausalGraph containing inferred nodes, links, and structure.
        """
        nodes: list[str] = []
        links: list[CausalLink] = []
        reasoning_chain: list[str] = []

        # Extract potential causes and effects from observation
        words = self._tokenize(observation)
        reasoning_chain.append(f"Observed scenario: {observation}")
        reasoning_chain.append(f"Extracted keywords: {', '.join(words)}")

        # Look for known causal patterns in the observation
        matched_chains = self._find_known_chains(observation.lower())
        for chain in matched_chains:
            cause, intermediate, effect = chain
            if cause not in nodes:
                nodes.append(cause)
            if intermediate not in nodes:
                nodes.append(intermediate)
            if effect not in nodes:
                nodes.append(effect)

            links.append(CausalLink(cause, intermediate, 0.9))
            links.append(CausalLink(intermediate, effect, 0.85))
            reasoning_chain.append(f"Found causal chain: {cause} -> {intermediate} -> {effect}")

        # Extract direct causal relationships from patterns
        direct_links = self._extract_direct_causal_links(observation)
        for link in direct_links:
            if link.cause not in nodes:
                nodes.append(link.cause)
            if link.effect not in nodes:
                nodes.append(link.effect)
            links.append(link)
            reasoning_chain.append(f"Direct causal link: {link.cause} -> {link.effect} (strength={link.strength})")

        # Identify root causes and leaf effects
        cause_set = {link.cause for link in links}
        effect_set = {link.effect for link in links}
        root_causes = tuple(cause_set - effect_set)
        leaf_effects = tuple(effect_set - cause_set)

        reasoning_chain.append(f"Identified root causes: {', '.join(root_causes) or 'none'}")
        reasoning_chain.append(f"Identified leaf effects: {', '.join(leaf_effects) or 'none'}")

        return CausalGraph(
            nodes=tuple(nodes),
            links=tuple(links),
            root_causes=root_causes,
            leaf_effects=leaf_effects,
        )

    async def counterfactual(
        self,
        scenario: str,
        hypothetical: str,
    ) -> CounterfactualResult:
        """Reason about counterfactual 'what if' scenarios.

        Given an original scenario and a hypothetical change,
        predict what would have happened differently.

        Args:
            scenario: The original scenario description.
            hypothetical: The hypothetical change to consider.

        Returns:
            CounterfactualResult with predicted outcome and reasoning.
        """
        reasoning_chain: list[str] = []
        reasoning_chain.append(f"Original scenario: {scenario}")
        reasoning_chain.append(f"Hypothetical change: {hypothetical}")

        # Analyze the scenario and hypothetical
        scenario_lower = scenario.lower()
        hypothetical_lower = hypothetical.lower()

        # Extract key elements from scenario
        scenario_elements = self._extract_key_elements(scenario)
        hypothetical_elements = self._extract_key_elements(hypothetical)

        reasoning_chain.append(f"Scenario elements: {', '.join(scenario_elements)}")
        reasoning_chain.append(f"Hypothetical elements: {', '.join(hypothetical_elements)}")

        # Determine the type of change
        change_type = self._classify_change(hypothetical_lower)
        reasoning_chain.append(f"Classified change type: {change_type}")

        # Predict outcome based on change type and causal relationships
        predicted_outcome, confidence = self._predict_counterfactual_outcome(
            scenario, scenario_lower, hypothetical_lower, change_type, scenario_elements, hypothetical_elements
        )

        reasoning_chain.append(f"Predicted outcome: {predicted_outcome}")
        reasoning_chain.append(f"Confidence: {confidence:.2f}")

        return CounterfactualResult(
            original_scenario=scenario,
            hypothetical_change=hypothetical,
            predicted_outcome=predicted_outcome,
            confidence=confidence,
            reasoning_chain=tuple(reasoning_chain),
        )

    async def analogical_reasoning(
        self,
        source_domain: str,
        target_domain: str,
    ) -> AnalogyResult:
        """Perform analogical reasoning between domains.

        Maps properties from source to target and infers new properties.

        Args:
            source_domain: The source domain for analogy.
            target_domain: The target domain for analogy.

        Returns:
            AnalogyResult with similarity score and inferred properties.
        """
        reasoning_chain: list[str] = []
        reasoning_chain.append(f"Source domain: {source_domain}")
        reasoning_chain.append(f"Target domain: {target_domain}")

        # Extract properties from source and target
        source_props = self._extract_domain_properties(source_domain)
        target_props = self._extract_domain_properties(target_domain)

        reasoning_chain.append(f"Source properties: {', '.join(source_props)}")
        reasoning_chain.append(f"Target properties: {', '.join(target_props)}")

        # Find common/mapped properties
        mapped_properties = self._find_mapped_properties(source_props, target_props)
        reasoning_chain.append(f"Mapped properties: {', '.join(mapped_properties)}")

        # Calculate similarity score
        similarity_score = self._calculate_similarity(source_props, target_props, mapped_properties)
        reasoning_chain.append(f"Similarity score: {similarity_score:.2f}")

        # Infer new properties for target based on source
        inferred_properties = self._infer_properties(source_props, target_props, mapped_properties)
        reasoning_chain.append(f"Inferred properties: {', '.join(inferred_properties)}")

        return AnalogyResult(
            source=source_domain,
            target=target_domain,
            similarity_score=similarity_score,
            mapped_properties=tuple(mapped_properties),
            inferred_properties=tuple(inferred_properties),
        )

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into words."""
        words = re.findall(r"\b[a-z_]+\b", text.lower())
        return [w for w in words if len(w) > 2]

    def _find_known_chains(self, text: str) -> list[tuple[str, str, str]]:
        """Find known causal chains in text."""
        matched = []
        for cause, intermediate, effect in KNOWN_CAUSAL_CHAINS:
            if cause in text or effect in text:
                matched.append((cause, intermediate, effect))
        return matched

    def _extract_direct_causal_links(self, observation: str) -> list[CausalLink]:
        """Extract direct causal links from observation using patterns."""
        links = []
        obs_lower = observation.lower()

        for cause_pattern, _effect_pattern, strength in CAUSAL_PATTERNS:
            if re.search(cause_pattern, obs_lower):
                # Find what follows the cause pattern
                # Match optional placeholder in braces followed by the effect word
                match = re.search(f"{cause_pattern}\\s+(?:\\{{\\w+\\}}\\s+)?(\\w+)", obs_lower)
                if match:
                    effect_word = match.group(1)
                    # Clean up the cause pattern for display
                    cause_clean = cause_pattern.replace("\\b", "").replace("\\s", " ").replace("?", "")
                    links.append(CausalLink(cause_clean, effect_word, strength))

        return links

    def _extract_key_elements(self, text: str) -> list[str]:
        """Extract key elements from text."""
        # Common semantic categories
        categories = {
            "agent": ["person", "human", "worker", "student", "teacher", "doctor"],
            "action": ["work", "study", "run", "eat", "sleep", "drive", "write"],
            "object": ["car", "book", "computer", "phone", "food", "water"],
            "state": ["happy", "sad", "tired", "hungry", "cold", "hot"],
            "location": ["home", "office", "school", "store", "park"],
        }

        text_lower = text.lower()
        elements = []

        for _category, keywords in categories.items():
            for keyword in keywords:
                if keyword in text_lower:
                    elements.append(keyword)

        # Add any capitalized terms as potential entities
        words = re.findall(r"\b[A-Z][a-z]+\b", text)
        elements.extend(words[:5])  # Limit to 5 entities

        return list(set(elements))

    def _classify_change(self, hypothetical: str) -> str:
        """Classify the type of change in hypothetical."""
        if any(word in hypothetical for word in ["remove", "delete", "without", "no", "not"]):
            return "removal"
        if any(word in hypothetical for word in ["add", "insert", "with", "include", "extra"]):
            return "addition"
        if any(word in hypothetical for word in ["change", "modify", "alter", "switch"]):
            return "modification"
        if any(word in hypothetical for word in ["increase", "more", "higher", "bigger"]):
            return "increase"
        if any(word in hypothetical for word in ["decrease", "less", "lower", "smaller"]):
            return "decrease"
        return "unknown"

    def _predict_counterfactual_outcome(
        self,
        scenario: str,
        scenario_lower: str,
        hypothetical_lower: str,
        change_type: str,
        scenario_elements: list[str],
        hypothetical_elements: list[str],
    ) -> tuple[str, float]:
        """Predict outcome of counterfactual scenario."""
        # Look for inverse relationships based on change type
        if change_type == "removal":
            # If something is removed, its effects should be negated
            for cause, _intermediate, effect in KNOWN_CAUSAL_CHAINS:
                if cause in scenario_lower:
                    outcome = f"If {cause} were removed, {effect} would not occur"
                    return outcome, 0.75

        elif change_type == "addition":
            # If something is added, its effects should be enhanced
            for cause, _intermediate, effect in KNOWN_CAUSAL_CHAINS:
                if cause in hypothetical_lower:
                    outcome = f"If {cause} were added, {effect} would occur"
                    return outcome, 0.7

        elif change_type == "increase":
            outcome = "The effect would be amplified or more pronounced"
            return outcome, 0.65

        elif change_type == "decrease":
            outcome = "The effect would be reduced or less pronounced"
            return outcome, 0.65

        # Default outcome based on general causal reasoning
        outcome = f"The scenario outcome would change based on the nature of '{hypothetical_lower}'"
        return outcome, 0.5

    def _extract_domain_properties(self, domain: str) -> list[str]:
        """Extract properties from a domain."""
        # Define common domain property mappings
        domain_properties: dict[str, list[str]] = {
            "atom": ["nucleus", "electrons", "protons", "neutrons", "orbit", "energy"],
            "solar_system": ["sun", "planets", "orbit", "gravity", "star", "satellite"],
            "cell": ["membrane", "nucleus", "cytoplasm", "organelles", "DNA", "wall"],
            "computer": ["CPU", "memory", "storage", "input", "output", "software", "hardware"],
            "human_body": ["heart", "brain", "lungs", "organs", "blood", "nervous_system"],
            "company": ["employees", "products", "revenue", "customers", "management", "structure"],
            "ecosystem": ["species", "food_chain", "environment", "resources", "climate", "biodiversity"],
            "family": ["parents", "children", "relationships", "love", "support", "heritage"],
            "game": ["players", "rules", "score", "strategy", "winning", "competition"],
            "story": ["characters", "plot", "conflict", "resolution", "setting", "narrative"],
        }

        domain_lower = domain.lower()
        for domain_name, properties in domain_properties.items():
            if domain_name in domain_lower or any(p in domain_lower for p in properties):
                return properties

        # Fallback: extract from domain name
        words = self._tokenize(domain)
        return words if words else ["unknown"]

    def _find_mapped_properties(
        self,
        source_props: list[str],
        target_props: list[str],
    ) -> list[str]:
        """Find common/mapped properties between source and target."""
        source_set = set(source_props)
        target_set = set(target_props)
        common = source_set & target_set

        # Also map properties that are semantically similar
        similar_mappings = []
        for s_prop in source_set:
            for t_prop in target_set:
                if s_prop != t_prop and (s_prop in t_prop or t_prop in s_prop):
                    similar_mappings.append(f"{s_prop}<->{t_prop}")

        return list(common) + similar_mappings

    def _calculate_similarity(
        self,
        source_props: list[str],
        target_props: list[str],
        mapped_properties: list[str],
    ) -> float:
        """Calculate similarity score between domains."""
        if not source_props or not target_props:
            return 0.0

        source_set = set(source_props)
        target_set = set(target_props)

        # Jaccard similarity
        intersection = len(source_set & target_set)
        union = len(source_set | target_set)

        if union == 0:
            return 0.0

        # Base similarity from Jaccard
        base_similarity = intersection / union

        # Boost for structural similarity in mappings
        mapping_bonus = len(mapped_properties) * 0.05

        return min(1.0, base_similarity + mapping_bonus)

    def _infer_properties(
        self,
        source_props: list[str],
        target_props: list[str],
        mapped_properties: list[str],
    ) -> list[str]:
        """Infer new properties for target based on source properties."""
        inferred: list[str] = []

        source_set = set(source_props)
        target_set = set(target_props)

        # Properties in source but not in target that could be inferred
        for prop in source_set:
            if prop not in target_set:
                # Check if there's a similar mapped property
                for mapped in mapped_properties:
                    if prop in mapped:
                        inferred.append(f"inferred_{prop}")

        # If similarity is high, infer structural properties
        similarity = self._calculate_similarity(source_props, target_props, mapped_properties)
        if similarity > 0.3:
            # Infer that target might share structure with source
            if len(source_props) > len(target_props):
                inferred.append("structural_complexity")
            if "organelles" in source_set or "components" in source_set:
                inferred.append("internal_structure")

        return inferred
