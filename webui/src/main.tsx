import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import "@/styles/global.css";
import { App } from "./App";
import { PapersPage } from "@/pages/PapersPage";
import { PaperDetailPage } from "@/pages/PaperDetailPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { FilesPage } from "@/pages/FilesPage";
import { VoicesPage } from "@/pages/VoicesPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <PapersPage /> },
      { path: "papers/:paperId", element: <PaperDetailPage /> },
      { path: "files", element: <FilesPage /> },
      { path: "voices", element: <VoicesPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
