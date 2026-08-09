import { UI_TERMS } from './uiTerminology';
export function getRoleDisplayLabel(roleId) {
    if (roleId === 'docs') {
        return UI_TERMS.roles.architect;
    }
    if (Object.prototype.hasOwnProperty.call(UI_TERMS.roles, roleId)) {
        return UI_TERMS.roles[roleId];
    }
    return roleId;
}
