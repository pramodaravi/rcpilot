# Assets/Scenes

This folder is intentionally empty — the project uses a **procedural boot**
pattern so we don't have to ship binary `.unity` scene files that are easy to
corrupt in a source-only distribution.

On first open:

1. File → New Scene → Basic (Built-in) → Create.
2. Save as `Main.unity` in this folder.
3. Delete the default `Main Camera` + `Directional Light` from the Hierarchy.
4. GameObject → Create Empty → name `Boot`.
5. Inspector → Add Component → `Bootstrapper`.
6. File → Build Settings → Add Open Scenes (so this scene is the startup).

That's it. Press Play and `Bootstrapper.Start()` creates the camera, cockpit,
HUD, network layer, and everything else.

See `docs/unity-setup.md` for the full checklist.
