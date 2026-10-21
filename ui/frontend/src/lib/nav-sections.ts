import {
  TerminalSquareIcon,
  MusicIcon,
  BriefcaseIcon,
  UsersIcon,
  BookOpenIcon,
  ClapperboardIcon,
  BotIcon,
  ActivityIcon,
  UtensilsCrossedIcon,
  PaletteIcon,
  type LucideIcon,
} from "lucide-react"

export type NavSection = {
  title: string
  path: string
  icon: LucideIcon
}

export const NAV_SECTIONS: NavSection[] = [
  { title: "Command Center", path: "/", icon: TerminalSquareIcon },
  { title: "Music", path: "/music", icon: MusicIcon },
  { title: "Business", path: "/business", icon: BriefcaseIcon },
  { title: "Restaurant", path: "/restaurant", icon: UtensilsCrossedIcon },
  { title: "Design", path: "/design", icon: PaletteIcon },
  { title: "War Rooms", path: "/war-rooms", icon: UsersIcon },
  { title: "Knowledge", path: "/knowledge", icon: BookOpenIcon },
  { title: "Studio", path: "/studio", icon: ClapperboardIcon },
  { title: "Agents", path: "/agents", icon: BotIcon },
  { title: "System", path: "/system", icon: ActivityIcon },
]
