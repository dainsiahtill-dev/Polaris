import { useState, useCallback } from 'react';
const MAX_EVENTS = 500;
export const useTestEvents = () => {
    const [events, setEvents] = useState([]);
    const addEvent = useCallback((event) => {
        setEvents((prev) => {
            const next = [...prev, event];
            if (next.length <= MAX_EVENTS)
                return next;
            return next.slice(next.length - MAX_EVENTS);
        });
    }, []);
    const resetEvents = useCallback(() => setEvents([]), []);
    return { events, addEvent, resetEvents };
};
