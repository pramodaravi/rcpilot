using UnityEngine;
using RcPilot.UI;

namespace RcPilot.Assists
{
    /// <summary>
    /// Dev-only component that cycles the assist level on Tab. Useful while
    /// tuning without the menu system open. Fires a toast so the driver can
    /// see which tier they just jumped to.
    ///
    /// Kept separate from AssistController so the controller stays free of
    /// input-polling concerns (AssistController runs in FixedUpdate-adjacent
    /// timing; input is best read on Update).
    /// </summary>
    public class AssistLevelCycler : MonoBehaviour
    {
        private AssistController _ctrl;
        private HUDController _hud;

        public void Bind(AssistController ctrl, HUDController hud)
        {
            _ctrl = ctrl;
            _hud = hud;
        }

        private void Update()
        {
            if (_ctrl == null) return;
            if (UnityEngine.Input.GetKeyDown(KeyCode.Tab))
            {
                int next = ((int)_ctrl.level + 1) % 4;
                var lvl = (AssistLevel)next;
                _ctrl.SetLevel(lvl);
                _hud?.toasts?.Show($"ASSIST: {lvl}", UiTheme.Accent, 1.4f);
            }
        }
    }
}
