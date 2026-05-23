import { UI_TERMS } from './uiTerminology';

export type KnownRoleId = keyof typeof UI_TERMS.roles | 'docs';

export function getRoleDisplayLabel(roleId: string): string {
  if (roleId === 'docs') {
    return UI_TERMS.roles.architect;
  }
  if (Object.prototype.hasOwnProperty.call(UI_TERMS.roles, roleId)) {
    return UI_TERMS.roles[roleId as keyof typeof UI_TERMS.roles];
  }
  return roleId;
}
