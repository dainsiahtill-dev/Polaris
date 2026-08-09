export function isLancedbExplicitlyBlocked(status) {
    return status?.ok === false;
}
