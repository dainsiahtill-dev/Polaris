export function parseRealtimePatchForDiff({ patch, operation, oldContent, newContent, }) {
    if (!patch) {
        return {
            oldValue: oldContent || '',
            newValue: newContent || '',
        };
    }
    const lines = patch.split('\n');
    const hasUnifiedMarkers = lines.some((line) => line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@'));
    if (!hasUnifiedMarkers) {
        if (operation === 'delete') {
            return { oldValue: patch, newValue: '' };
        }
        return { oldValue: '', newValue: patch };
    }
    const oldLines = [];
    const newLines = [];
    for (const line of lines) {
        if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) {
            continue;
        }
        if (line.startsWith('-')) {
            oldLines.push(line.substring(1));
            continue;
        }
        if (line.startsWith('+')) {
            newLines.push(line.substring(1));
            continue;
        }
        if (line.startsWith(' ')) {
            oldLines.push(line.substring(1));
            newLines.push(line.substring(1));
        }
    }
    return {
        oldValue: oldLines.join('\n'),
        newValue: newLines.join('\n'),
    };
}
