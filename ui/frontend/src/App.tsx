import { Routes, Route } from "react-router-dom"

import { AppShell } from "@/components/layout/app-shell"
import { CommandCenter } from "@/pages/command-center"
import { AgentsPage } from "@/pages/agents-page"
import { WarRoomsPage } from "@/pages/war-rooms-page"
import { SystemPage } from "@/pages/system-page"
import { KnowledgePage } from "@/pages/knowledge-page"
import { BusinessPage } from "@/pages/business-page"
import { MusicPage } from "@/pages/music-page"
import { StudioPage } from "@/pages/studio-page"
import { RestaurantPage } from "@/pages/restaurant-page"
import { DesignPage } from "@/pages/design-page"
import { LoginPage } from "@/pages/login-page"

function App() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route index element={<CommandCenter />} />
        <Route path="music" element={<MusicPage />} />
        <Route path="business" element={<BusinessPage />} />
        <Route path="restaurant" element={<RestaurantPage />} />
        <Route path="design" element={<DesignPage />} />
        <Route path="war-rooms" element={<WarRoomsPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="studio" element={<StudioPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="system" element={<SystemPage />} />
      </Route>
    </Routes>
  )
}

export default App
