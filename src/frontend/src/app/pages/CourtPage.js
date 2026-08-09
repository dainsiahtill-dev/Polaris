import { jsx as _jsx } from "react/jsx-runtime";
import { CourtContainer } from '../components/court';
import { devLogger } from '@/app/utils/devLogger';
export function CourtPage() {
    return (_jsx("div", { className: "w-full h-screen bg-slate-950", children: _jsx(CourtContainer, { defaultCameraMode: "overview", enableRealtime: true, onActorSelect: (actor) => {
                if (actor) {
                    devLogger.debug('Selected actor:', actor);
                }
            } }) }));
}
export default CourtPage;
