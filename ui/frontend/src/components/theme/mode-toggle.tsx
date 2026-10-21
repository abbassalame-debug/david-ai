import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme/theme-provider"

export function ModeToggle() {
  const { theme, setTheme } = useTheme()

  return (
    <Button variant="outline" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
      Toggle theme (current: {theme})
    </Button>
  )
}
