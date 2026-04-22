// Global State Management - Materialization Phase
import React, { createContext, useContext, useReducer, useCallback, useMemo } from 'react';

type Action<T = any> = {
  type: string;
  payload?: T;
};

type Reducer<S = any> = (state: S, action: Action) => S;

type Selector<T, S = any> = (state: S) => T;

interface Store<S> {
  state: S;
  dispatch: (action: Action) => void;
}

interface GlobalStoreState {
  user: { id: string; name: string; email: string } | null;
  theme: 'light' | 'dark';
  notifications: Array<{ id: string; message: string; type: 'success' | 'error' | 'info' }>;
  loading: Record<string, boolean>;
}

const initialState: GlobalStoreState = {
  user: null,
  theme: 'light',
  notifications: [],
  loading: {},
};

function globalReducer(state: GlobalStoreState, action: Action): GlobalStoreState {
  switch (action.type) {
    case 'SET_USER':
      return { ...state, user: action.payload };
    case 'CLEAR_USER':
      return { ...state, user: null };
    case 'SET_THEME':
      return { ...state, theme: action.payload };
    case 'ADD_NOTIFICATION':
      return {
        ...state,
        notifications: [...state.notifications, action.payload],
      };
    case 'REMOVE_NOTIFICATION':
      return {
        ...state,
        notifications: state.notifications.filter(n => n.id !== action.payload),
      };
    case 'SET_LOADING':
      return {
        ...state,
        loading: { ...state.loading, ...action.payload },
      };
    default:
      return state;
  }
}

const GlobalStoreContext = createContext<Store<GlobalStoreState> | null>(null);

export function GlobalStoreProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(globalReducer, initialState);

  const store = useMemo<Store<GlobalStoreState>>(
    () => ({
      state,
      dispatch,
    }),
    [state]
  );

  return (
    <GlobalStoreContext.Provider value={store}>
      {children}
    </GlobalStoreContext.Provider>
  );
}

export function useGlobalStore(): Store<GlobalStoreState> {
  const context = useContext(GlobalStoreContext);
  if (!context) {
    throw new Error('useGlobalStore must be used within GlobalStoreProvider');
  }
  return context;
}

export function useSelector<T>(selector: Selector<T, GlobalStoreState>): T {
  const { state } = useGlobalStore();
  return useMemo(() => selector(state), [selector, state]);
}

export function useDispatch() {
  const { dispatch } = useGlobalStore();
  return dispatch;
}

export function useUser() {
  return useSelector(state => state.user);
}

export function useTheme() {
  return useSelector(state => state.theme);
}

export function useNotifications() {
  return useSelector(state => state.notifications);
}

export function useLoading(key?: string) {
  return useSelector(state => (key ? state.loading[key] : state.loading));
}

export function useActions() {
  const dispatch = useDispatch();
  
  return useMemo(
    () => ({
      setUser: (user: GlobalStoreState['user']) =>
        dispatch({ type: 'SET_USER', payload: user }),
      clearUser: () => dispatch({ type: 'CLEAR_USER' }),
      setTheme: (theme: 'light' | 'dark') =>
        dispatch({ type: 'SET_THEME', payload: theme }),
      addNotification: (notification: GlobalStoreState['notifications'][0]) =>
        dispatch({ type: 'ADD_NOTIFICATION', payload: notification }),
      removeNotification: (id: string) =>
        dispatch({ type: 'REMOVE_NOTIFICATION', payload: id }),
      setLoading: (loading: Record<string, boolean>) =>
        dispatch({ type: 'SET_LOADING', payload: loading }),
    }),
    [dispatch]
  );
}

export { GlobalStoreContext };
export type { GlobalStoreState, Action, Reducer };
