/** Re-export barrel for the expanded-tech-evidence matrix modules.
 *
 * Split from the historical monolithic expandedTechEvidenceMatrix.ts so the
 * large data tables, helpers and probe collectors each live in a focused
 * module. Consumers importing "./helpers/expandedTechEvidenceMatrix" resolve
 * here unchanged.
 */

export { type BackendConnection, type CandidateRuntimeCoverageRow, type CandidateRuntimeCoverageStatus, type CoreEvidenceSinkName, type CoreEvidenceSinkPlacement, type CoreRuntimeEvidencePlacement, type CoreRuntimeEvidencePlacementRow, type EvidenceProbe, type EvidenceRef, type EvidenceStatus, type ExpandedCandidateRuntimeCoverage, type ExpandedTechCandidate, type ExpandedTechEvidenceReport } from "./types";
export { CANDIDATE_RUNTIME_PROBE_IDS, CANDIDATE_SOURCE_PROBE_IDS, CORE_TECH_IDS, EXPANDED_TECH_CANDIDATES } from "./data";
export { buildCoreRuntimeEvidencePlacement, buildExpandedCandidateRuntimeCoverage, findRoleSessionKernelAuditEvent, getBackendInfoFromPage, requestJson, requestText, resolveBackendInfoSnapshot, type RoleSessionKernelAuditMatch } from "./matrix_helpers";
export { assertExpandedTechEvidenceMatrix, collectExpandedTechEvidenceMatrix, writeExpandedTechEvidenceMatrix } from "./matrix";
