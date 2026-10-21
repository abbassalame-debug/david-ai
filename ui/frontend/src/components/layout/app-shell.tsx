import { useEffect, useState } from "react"
import { Link, Outlet, useLocation } from "react-router-dom"
import { LogOutIcon } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarInset,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"
import { ModeToggle } from "@/components/theme/mode-toggle"
import { CommandPalette } from "@/components/layout/command-palette"
import { NAV_SECTIONS } from "@/lib/nav-sections"
import { Button } from "@/components/ui/button"
import { getAuthStatus, signOut } from "@/lib/auth"

// Shown only while the HUD login is on and this browser is signed in (workstream 3).
function SignOutButton() {
  const [show, setShow] = useState(false)
  useEffect(() => {
    getAuthStatus().then((s) => setShow(!!s && s.login === "on" && s.signed_in))
  }, [])
  if (!show) return null
  return (
    <Button variant="ghost" size="sm" onClick={() => void signOut()} aria-label="Sign out">
      <LogOutIcon />
      <span>Sign out</span>
    </Button>
  )
}

function useCurrentSectionTitle() {
  const location = useLocation()
  const match = NAV_SECTIONS.find((section) =>
    section.path === "/" ? location.pathname === "/" : location.pathname.startsWith(section.path)
  )
  return match?.title ?? "David AI"
}

function AppSidebar() {
  const location = useLocation()

  return (
    <Sidebar collapsible="icon">
      <SidebarContent>
        <SidebarMenu>
          {NAV_SECTIONS.map((section) => {
            const isActive =
              section.path === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(section.path)

            return (
              <SidebarMenuItem key={section.path}>
                <SidebarMenuButton asChild tooltip={section.title} isActive={isActive}>
                  <Link to={section.path}>
                    <section.icon />
                    <span>{section.title}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )
          })}
        </SidebarMenu>
      </SidebarContent>
    </Sidebar>
  )
}

export function AppShell() {
  const title = useCurrentSectionTitle()

  return (
    <SidebarProvider className="h-svh overflow-hidden">
      <AppSidebar />
      <SidebarInset className="min-h-0">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-5" />
          <h1 className="text-sm font-medium">{title}</h1>
          <div className="ml-auto flex items-center gap-2">
            <SignOutButton />
            <ModeToggle />
          </div>
        </header>
        <main className="flex flex-1 flex-col overflow-auto min-h-0">
          <Outlet />
        </main>
      </SidebarInset>
      <CommandPalette />
    </SidebarProvider>
  )
}
