export class DeepTestViewAdapter {
    createViewState() {
        return {
            testMode: 'auto',
            currentView: 'hall',
            isFullscreen: false,
            sidebarCollapsed: false,
            templatePanelOpen: false
        };
    }
    adaptToView(unifiedData) {
        return {
            roles: Object.values(unifiedData.roles).map(role => ({
                id: role.id,
                name: role.name,
                requirements: role.requirements,
                assignment: role.assignment,
                readiness: role.attributes.readiness_status,
                lastInterview: role.attributes.testing?.interview_history?.[0],
                capabilityAssessments: role.attributes.testing?.capability_assessments
            })),
            providers: Object.values(unifiedData.providers).map(provider => ({
                id: provider.id,
                name: provider.name,
                type: provider.type,
                connectivityStatus: provider.attributes.connectivity_status,
                lastTest: provider.attributes.last_test_timestamp,
                thinkingCapability: provider.attributes.thinking_capability,
                capabilityScores: provider.attributes.testing?.capability_scores
            })),
            testSessions: Object.values(unifiedData.extensions.testing?.interview_results || {})
                .filter(result => result.status === 'running')
                .map(result => ({
                id: result.id,
                status: 'running',
                type: 'interview',
                targetId: result.role_id,
                startTime: result.start_time
            })),
            testResults: {
                interviews: unifiedData.extensions.testing?.interview_results || {},
                connectivity: unifiedData.extensions.testing?.connectivity_tests || {},
                capabilities: unifiedData.extensions.testing?.capability_scores || {}
            }
        };
    }
    adaptFromView(viewData, unifiedData) {
        const updates = {
            extensions: {
                ...unifiedData.extensions,
                testing: {
                    ...unifiedData.extensions.testing,
                    interview_results: viewData.testResults.interviews,
                    connectivity_tests: viewData.testResults.connectivity,
                    capability_scores: viewData.testResults.capabilities,
                    test_preferences: unifiedData.extensions.testing?.test_preferences || {
                        auto_run_connectivity: false,
                        auto_run_interviews: false,
                        concurrency: 1
                    }
                }
            }
        };
        // Update Role Status based on new interview results
        // Iterate over updated interviews in viewData and update corresponding roles
        // This logic relies on viewData being the source of truth for recent tests
        // For simplicity here, we assume if viewData has new results, we should update attributes
        // But logically, the test runner updates the result, and we might need to actively push that to role attributes
        // A better approach might be: when a test finishes, an action updates the unified config via a specific reducer case,
        // rather than "adapting from view" which implies the view state drives the data.
        // However, for this adapter pattern, we return the updates.
        return updates;
    }
}
