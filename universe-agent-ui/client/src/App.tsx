/** App shell: TopBar, sidebar and rail chrome on the Universe palette. */
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Redirect, Route, Switch, useLocation, useSearch } from "wouter";
import { lazy, Suspense, useEffect } from "react";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import UniverseConnector from "./components/desktop/UniverseConnector";
import TopBar from "@/components/desktop/TopBar";
import TransparencyBar from "@/components/desktop/TransparencyBar";
import CommandPalette from "@/components/desktop/CommandPalette";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { useUi } from "@/stores/ui";

const LazyChat = lazy(() => import("./pages/Chat"));
const LazySettings = lazy(() => import("./pages/Settings"));
const LazyAutomations = lazy(() => import("./pages/Automations"));
const LazyBots = lazy(() => import("./pages/Bots"));
const LazyStudio = lazy(() => import("./pages/Studio"));

function Router() {
  return (
    <Switch>
      {/* the desktop work surface */}
      <Route path={"/"} component={() => <Redirect to="/chat" />} />
      {/* direct .html entry (app://universe/index.html) must never 404 */}
      <Route path={"/index.html"} component={() => <Redirect to="/chat" />} />
      <Route
        path={"/settings"}
        component={() => (
          <Suspense fallback={<div className="page-loading" />}>
            <LazySettings />
          </Suspense>
        )}
      />
        <Route
          path={"/automations"}
          component={() => (
            <Suspense fallback={<div className="page-loading" />}>
              <LazyAutomations />
            </Suspense>
          )}
        />
        <Route
          path={"/chat"}
          component={() => (
            <Suspense fallback={<div className="page-loading" />}>
              <LazyChat />
            </Suspense>
          )}
        />
        <Route
          path={"/bots"}
          component={() => (
            <Suspense fallback={<div className="page-loading" />}>
              <LazyBots />
            </Suspense>
          )}
        />
        <Route
          path={"/studio"}
          component={() => (
            <Suspense fallback={<div className="page-loading" />}>
              <LazyStudio />
            </Suspense>
          )}
        />
        <Route path={"/404"} component={NotFound} />
        {/* Final fallback route */}
        <Route component={NotFound} />
      </Switch>
  );
}

// NOTE: About Theme
// - First choose a default theme according to your design style (dark or light bg), than change color palette in index.css
//   to keep consistent foreground/background color across components
// - If you want to make theme switchable, pass `switchable` ThemeProvider and use `useTheme` hook

/** The shell chrome: the TopBar owns the window controls + session
 *  navigation, the palette + shortcuts live above every page, and every
 *  location change is recorded into the nav history the back/forward
 *  buttons walk. wouter's useLocation returns the PATHNAME only — every
 *  /chat?goal=… switch would record the identical "/chat" and the history
 *  would dedupe into inertness, so the recorded entry carries the query
 *  too. */
function ShellChrome() {
  const [location] = useLocation();
  const search = useSearch();
  const recordNav = useUi(s => s.recordNav);
  useKeyboardShortcuts();
  useEffect(() => {
    recordNav(search ? `${location}?${search}` : location);
  }, [location, search, recordNav]);
  return (
    <>
      <TopBar />
      <TransparencyBar />
      <CommandPalette />
    </>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider
        defaultTheme="dark"
        // switchable
      >
        <TooltipProvider>
          <Toaster />
          {/* one live socket for the whole app — survives route changes so the
              memory universe keeps receiving events from any surface */}
          <UniverseConnector />
          <ShellChrome />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
