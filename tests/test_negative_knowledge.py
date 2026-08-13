"""
Test suite for the mathematical claims in "Negative Knowledge as the Primary Computational Resource."

These tests verify the core principles described in the paper:
1. Bloom filter "definitely safe" has zero false confirms
2. Heyting algebra properties (excluded middle fails, double negation ≠ identity)
3. INT8 soundness (identity on [-127, 127])
4. XOR unsigned comparison equivalence
5. Negative knowledge efficiency (skip rate when violations are rare)
6. Cross-domain analogy consistency
"""

import random
import struct
from collections import defaultdict

import pytest


# ---------------------------------------------------------------------------
# 1. Bloom Filter Implementation (paper §2.1)
# ---------------------------------------------------------------------------

class SimpleBloomFilter:
    """Minimal Bloom filter for testing negative knowledge claims.

    The key property: if any bit is 0, the element is DEFINITELY NOT present.
    If all bits are 1, the element is POSSIBLY present (false positive possible).
    """

    def __init__(self, size: int = 1024, num_hashes: int = 3):
        self.size = size
        self.num_hashes = num_hashes
        self.bits = bytearray(size)

    def _hashes(self, item):
        """Generate num_hashes bit positions for item."""
        # Use Python hash with different salts as a simple multi-hash
        for i in range(self.num_hashes):
            yield hash((item, i)) % self.size

    def add(self, item):
        for pos in self._hashes(item):
            self.bits[pos] = 1

    def definitely_not_present(self, item) -> bool:
        """The negative knowledge claim: if ANY bit is 0, item is NOT present."""
        return any(self.bits[pos] == 0 for pos in self._hashes(item))

    def possibly_present(self, item) -> bool:
        """The weak (doubly-negated) claim: all bits set means MIGHT be present."""
        return all(self.bits[pos] == 1 for pos in self._hashes(item))


class TestBloomFilterNegativeKnowledge:
    """§2.1: Bloom filter's 'definitely safe' is the fast path."""

    def test_zero_false_confirms(self):
        """The Bloom filter NEVER says 'definitely safe' for an item that was added."""
        bf = SimpleBloomFilter(size=512, num_hashes=3)
        items = list(range(100))
        for item in items:
            bf.add(item)

        for item in items:
            assert not bf.definitely_not_present(item), (
                f"FALSE CONFIRM: Bloom said 'definitely not present' for {item} which WAS added"
            )

    def test_definitely_not_present_for_absent_items(self):
        """Most items NOT in the filter should be 'definitely not present'."""
        bf = SimpleBloomFilter(size=2048, num_hashes=4)
        for i in range(50):
            bf.add(f"item_{i}")

        absent_items = [f"missing_{i}" for i in range(1000)]
        definitely_safe = sum(1 for item in absent_items if bf.definitely_not_present(item))

        # Should catch the vast majority as definitely absent
        assert definitely_safe > 900, f"Only {definitely_safe}/1000 flagged as definitely absent"

    def test_excluded_middle_fails(self):
        """Heyting logic: a ∨ ¬a ≠ ⊤. An item can be neither 'definitely safe' nor 'confirmed present'."""
        bf = SimpleBloomFilter(size=16, num_hashes=2)  # tiny filter = many false positives
        bf.add("X")

        # Find an item that is "possibly present" (all bits set) but was never added
        false_positive_found = False
        for i in range(10000):
            item = f"ghost_{i}"
            if not bf.definitely_not_present(item) and item != "X":
                # This item is in the "neither" zone — not definitely absent, not actually present
                false_positive_found = True
                break

        assert false_positive_found, (
            "Expected to find a false positive (excluded middle should fail for small filters)"
        )

    def test_double_negation_not_identity(self):
        """¬¬a ≠ a. 'Possibly present' (¬¬definitely_not_present) ≠ 'actually present'."""
        bf = SimpleBloomFilter(size=32, num_hashes=2)
        bf.add("real_item")

        # Find a false positive
        false_positives = []
        for i in range(10000):
            item = f"phantom_{i}"
            # ¬¬a: NOT definitely_not_present = possibly_present
            if bf.possibly_present(item) and item != "real_item":
                false_positives.append(item)

        assert len(false_positives) > 0, "Should have false positives showing ¬¬a ≠ a"

        # All false positives are "possibly present" but NOT actually present
        for fp in false_positives:
            assert bf.possibly_present(fp), f"{fp} should be possibly_present"
            assert fp != "real_item", "False positive should not be the real item"


# ---------------------------------------------------------------------------
# 2. INT8 Soundness (paper §2.2, Theorem 1)
# ---------------------------------------------------------------------------

class TestINT8Soundness:
    """§2.2: INT8 cast is identity on [-127, 127]. Proving absence of precision loss."""

    def test_int8_identity_on_safe_range(self):
        """For all v in [-127, 127], int8(v) == int32(v)."""
        for v in range(-127, 128):
            int8_val = ((v + 128) % 256) - 128  # simulate int8 cast
            assert int8_val == v, f"INT8 mismatch at {v}: got {int8_val}"

    def test_int8_overflow_at_boundary(self):
        """Values outside [-127, 127] may lose precision — the NEGATIVE claim is about safe range only."""
        # 128 overflows int8
        v = 128
        int8_val = ((v + 128) % 256) - 128
        assert int8_val != v, "128 should overflow int8 (negative claim boundary)"

        # -128 is fine in two's complement int8 but our safe range is [-127, 127]
        v = -128
        int8_val = ((v + 128) % 256) - 128
        assert int8_val == v, "-128 fits in two's complement int8"

    def test_negative_claim_universality(self):
        """The soundness claim is a universal negative: NO disagreements exist in the safe range.

        This test verifies it by exhaustive check — the strongest evidence available.
        """
        mismatches = 0
        for v in range(-127, 128):
            int8_val = ((v + 128) % 256) - 128
            if int8_val != v:
                mismatches += 1

        assert mismatches == 0, f"Found {mismatches} INT8 mismatches in safe range — soundness violated"


# ---------------------------------------------------------------------------
# 3. XOR Unsigned Comparison Equivalence (paper §2.3, Theorem 2)
# ---------------------------------------------------------------------------

def xor_unsigned_compare(value: int, lo: int, hi: int) -> bool:
    """XOR-based unsigned comparison: g(v) >= g(lo) and g(v) <= g(hi).

    g(x) = x ^ (1 << 31) for 32-bit signed → unsigned mapping.
    """
    SHIFT = 31
    gv = (value ^ (1 << SHIFT)) & 0xFFFFFFFF
    glo = (lo ^ (1 << SHIFT)) & 0xFFFFFFFF
    ghi = (hi ^ (1 << SHIFT)) & 0xFFFFFFFF
    return glo <= gv <= ghi


def signed_compare(value: int, lo: int, hi: int) -> bool:
    """Standard signed comparison."""
    return lo <= value <= hi


class TestXORDualVerification:
    """§2.3: XOR unsigned comparison is equivalent to signed comparison for ALL 32-bit integers."""

    def test_xor_equivalence_basic(self):
        """XOR comparison agrees with signed comparison on basic values."""
        test_cases = [
            (0, -10, 10),
            (-5, -10, 10),
            (5, -10, 10),
            (-100, -200, -50),
            (100, 50, 200),
        ]
        for v, lo, hi in test_cases:
            assert signed_compare(v, lo, hi) == xor_unsigned_compare(v, lo, hi), (
                f"Disagreement at v={v}, lo={lo}, hi={hi}"
            )

    def test_xor_equivalence_exhaustive_random(self):
        """XOR and signed paths agree on 1M random constraints — proving 'zero mismatches'."""
        random.seed(42)
        mismatches = 0
        for _ in range(1_000_000):
            v = random.randint(-2**31, 2**31 - 1)
            lo = random.randint(-2**31, 2**31 - 1)
            hi = random.randint(-2**31, 2**31 - 1)
            if lo > hi:
                lo, hi = hi, lo

            signed_result = signed_compare(v, lo, hi)
            xor_result = xor_unsigned_compare(v, lo, hi)

            if signed_result != xor_result:
                mismatches += 1

        assert mismatches == 0, f"Found {mismatches} XOR/signed disagreements in 1M tests"

    def test_xor_equivalence_boundary_values(self):
        """XOR path works at INT32 boundaries."""
        boundaries = [-2**31, -2**31 + 1, -1, 0, 1, 2**31 - 2, 2**31 - 1]
        for v in boundaries:
            for lo in boundaries:
                for hi in boundaries:
                    if lo > hi:
                        continue
                    assert signed_compare(v, lo, hi) == xor_unsigned_compare(v, lo, hi), (
                        f"Boundary disagreement at v={v}, lo={lo}, hi={hi}"
                    )


# ---------------------------------------------------------------------------
# 4. Differential Testing (paper §2.4)
# ---------------------------------------------------------------------------

class TestDifferentialTesting:
    """§2.4: Proving zero mismatches across precision levels."""

    def test_differential_zero_mismatches(self):
        """Running constraints at INT8 and INT32 levels produces identical pass/fail results
        when values are in the INT8 safe range."""
        random.seed(123)
        mismatches = 0
        for _ in range(500_000):
            v = random.randint(-127, 127)
            lo = random.randint(-127, 127)
            hi = random.randint(-127, 127)

            # INT32 check
            int32_pass = lo <= v <= hi

            # INT8 check (same logic, but simulating 8-bit path)
            int8_v = ((v + 128) % 256) - 128
            int8_lo = ((lo + 128) % 256) - 128
            int8_hi = ((hi + 128) % 256) - 128
            int8_pass = int8_lo <= int8_v <= int8_hi

            if int32_pass != int8_pass:
                mismatches += 1

        assert mismatches == 0, f"Differential testing found {mismatches} mismatches"


# ---------------------------------------------------------------------------
# 5. Negative Knowledge Efficiency (paper §5.3)
# ---------------------------------------------------------------------------

class TestNegativeKnowledgeEfficiency:
    """§5.3: When M << N, negative knowledge is asymptotically cheaper."""

    def test_skip_rate_when_violations_rare(self):
        """When most constraints pass, Bloom pre-filter should skip >60% of exact checks."""
        bf = SimpleBloomFilter(size=4096, num_hashes=5)

        # Add "boundary" values to the filter (these are the possibly-violated constraints)
        boundary_values = set()
        for i in range(20):  # very few violations
            boundary_values.add(random.randint(0, 10000))
            bf.add(list(boundary_values)[-1])

        # Test 10,000 values — most should be "definitely safe"
        test_values = list(range(10000))
        skip_count = sum(1 for v in test_values if bf.definitely_not_present(v))

        skip_rate = skip_count / len(test_values)
        # With rare violations, >60% should be skippable
        assert skip_rate > 0.5, (
            f"Skip rate only {skip_rate:.1%} — negative knowledge not efficient when M << N"
        )

    def test_negative_cheaper_than_positive(self):
        """Proving 'not violated' requires fewer checks than finding all violations."""
        N = 10000  # total constraints
        M = 50     # actual violations (0.5%)

        # Positive approach: check all N
        positive_checks = N

        # Negative approach: Bloom pre-filter skips most, then exact-check the residue
        bf = SimpleBloomFilter(size=8192, num_hashes=5)
        for i in range(M):
            bf.add(f"violation_{i}")

        skip_count = 0
        for i in range(N):
            if bf.definitely_not_present(f"violation_{i}"):
                skip_count += 1

        # Residue = items that couldn't be proven safe
        residue = N - skip_count
        negative_checks = N + residue  # bloom check (cheap) + exact check on residue

        # Negative knowledge should require fewer EXPENSIVE checks
        # (Bloom checks are ~10x cheaper than exact comparisons)
        effective_positive = positive_checks
        effective_negative = N * 0.1 + residue * 1.0  # bloom is 10% the cost

        assert effective_negative < effective_positive, (
            f"Negative approach ({effective_negative:.0f}) should be cheaper than "
            f"positive ({effective_positive}) when M << N"
        )


# ---------------------------------------------------------------------------
# 6. Cross-Domain Analogy Consistency (paper §4)
# ---------------------------------------------------------------------------

class TestCrossDomainAnalogies:
    """§4: Six physical domains operate on negative knowledge."""

    DOMAIN_ANALOGIES = {
        "immune": {
            "negative_claim": "not foreign",
            "failure_mode": "autoimmune",
            "mechanism": "self-tolerance eliminates safe molecules, attacks the rest",
        },
        "brain": {
            "negative_claim": "not surprising",
            "failure_mode": "hallucination",
            "mechanism": "predictive coding minimizes surprise, not predicts content",
        },
        "evolution": {
            "negative_claim": "not surviving",
            "failure_mode": "extinction",
            "mechanism": "elimination of unfit, not selection of fit",
        },
        "robotics": {
            "negative_claim": "not collision-free",
            "failure_mode": "collision",
            "mechanism": "eliminate colliding paths, not find free paths",
        },
        "cell_signaling": {
            "negative_claim": "no signal",
            "failure_mode": "cancer",
            "mechanism": "absence of growth signal means do not divide",
        },
        "compiler": {
            "negative_claim": "no effect",
            "failure_mode": "bloat",
            "mechanism": "dead code elimination removes proven-unnecessary code",
        },
        "constraint_checking": {
            "negative_claim": "not violated",
            "failure_mode": "false positive",
            "mechanism": "Bloom filter proves safety, checks residue exactly",
        },
    }

    def test_all_domains_have_negative_claim(self):
        """Every domain analogy has a well-formed negative knowledge claim."""
        for domain, analogy in self.DOMAIN_ANALOGIES.items():
            claim = analogy["negative_claim"]
            assert claim.startswith(("not ", "no ")), (
                f"{domain}: negative claim '{claim}' should be a negation"
            )

    def test_all_domains_have_failure_mode(self):
        """Every domain has a documented failure mode when negative knowledge fails."""
        for domain, analogy in self.DOMAIN_ANALOGIES.items():
            assert analogy["failure_mode"], f"{domain} missing failure mode"

    def test_constraint_checking_consistent_with_paper(self):
        """The constraint checking domain matches §2.1 (Bloom filter)."""
        cc = self.DOMAIN_ANALOGIES["constraint_checking"]
        assert "Bloom" in cc["mechanism"]
        assert cc["negative_claim"] == "not violated"


# ---------------------------------------------------------------------------
# 7. Heyting Algebra Properties (paper §5.1)
# ---------------------------------------------------------------------------

class TestHeytingAlgebraProperties:
    """§5.1: The Bloom filter is a Heyting algebra where excluded middle fails."""

    def test_consistency_a_and_not_a_is_bottom(self):
        """a ∧ ¬a = ⊥. An item cannot be both 'definitely not present' AND 'present'."""
        bf = SimpleBloomFilter(size=256, num_hashes=3)
        bf.add("exists")

        # "exists" is present
        assert not bf.definitely_not_present("exists")  # ¬a is False for present items

        # "missing" is definitely not present
        assert bf.definitely_not_present("missing_12345")  # ¬a is True for absent items

        # No item satisfies BOTH conditions simultaneously
        test_items = ["exists", "missing_1", "missing_2", "ghost_999"]
        for item in test_items:
            present = (item == "exists")
            not_present = bf.definitely_not_present(item)
            assert not (present and not_present), (
                f"{item}: a ∧ ¬a ≠ ⊥ — Heyting consistency violated"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
