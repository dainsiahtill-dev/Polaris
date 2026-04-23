"""KernelOne authentication & authorization contracts.

Defines the canonical authentication interface for KernelOne.

Key security invariant (IRONWALL-1 CRITICAL #1):
    An empty or blank token string is NEVER treated as authenticated.
    AuthCheckerPort.check("") MUST return AuthResult(authenticated=False).
    This closes the authentication bypass where an empty KERNELONE_TOKEN
    was incorrectly accepted as valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class AuthStrength(str, Enum):
    """Strength classification of an authentication result."""

    NONE = "none"  # No credentials provided
    WEAK = "weak"  # Credentials present but not verified
    STRONG = "strong"  # Credentials verified cryptographically


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Immutable result of an authentication check.

    Attributes:
        authenticated: True only when the token is present and verified.
        strength: Classification of the authentication quality.
        principal: Identity string for the authenticated principal, or "".
        error: Error message if authentication failed, or "".
    """

    authenticated: bool
    strength: AuthStrength
    principal: str = ""
    error: str = ""

    @classmethod
    def denied(cls, error: str = "Authentication denied") -> AuthResult:
        """Factory: unauthenticated result with an error message."""
        return cls(authenticated=False, strength=AuthStrength.NONE, error=error)

    @classmethod
    def anonymous(cls) -> AuthResult:
        """Factory: unauthenticated but no error (no credentials provided)."""
        return cls(authenticated=False, strength=AuthStrength.NONE)

    def to_dict(self) -> dict[str, str]:
        return {
            "authenticated": str(self.authenticated),
            "strength": self.strength.value,
            "principal": self.principal,
            "error": self.error,
        }


@runtime_checkable
class AuthCheckerPort(Protocol):
    """Protocol for authentication checks within KernelOne.

    All implementations MUST enforce the following security constraints:

    1. **Empty token rejection**: check("") returns AuthResult(authenticated=False).
       There is no "default" or "guest" mode when the token is absent or blank.

    2. **Whitespace-only rejection**: check("   ") returns authenticated=False.
       Tokens containing only whitespace are treated the same as empty tokens.

    3. **Consistent principal**: When authenticated=True, the principal field
       MUST be populated with a non-empty identity string.

    4. **No silent failure**: Errors during the check (e.g., key vault unavailable)
       MUST return authenticated=False with a non-empty error message.
       They MUST NOT raise or return authenticated=True.

    Implementations:
        - StaticTokenAuthChecker: Verifies against a pre-configured static token.
        - EnvVarTokenAuthChecker: Reads KERNELONE_TOKEN from environment.
        - JWTTokenAuthChecker: Cryptographically verifies a JWT (placeholder).
    """

    def check(self, token: str) -> AuthResult:
        """Verify a token and return the authentication result.

        Args:
            token: The raw credential to verify. May be empty or whitespace-only.

        Returns:
            AuthResult with the authentication decision.
            Always returns authenticated=False for empty/whitespace tokens.

        Example::

            result = checker.check(request.headers.get("Authorization", ""))
            if not result.authenticated:
                raise HTTPException(401, detail=result.error)
        """
        ...

    def strength(self, token: str) -> AuthStrength:
        """Classify the strength of the provided token without full verification.

        This is a lightweight pre-check useful for logging and metrics
        without the cost of full cryptographic verification.

        Args:
            token: The credential to classify.

        Returns:
            AuthStrength.NONE for empty/whitespace tokens.
        """
        ...
