using UnityEngine;
using RcPilot.Core;
using RcPilot.Video;

namespace RcPilot.Sim
{
    /// <summary>
    /// Mounts an FPV camera on the sim car, renders to a RenderTexture, and
    /// feeds that texture into the cockpit's main screen slot.
    ///
    /// The cockpit normally points its screen materials at Texture2D buffers
    /// that the VideoBridgeClient paints. In sim mode we bypass the bridge
    /// and hand the cockpit a RenderTexture instead — same slot, different
    /// source. CockpitBuilder.SetCameraSource() handles the re-bind.
    ///
    /// Also sets up a secondary "chase cam" rendertexture for cam1 so the
    /// secondary screen shows something useful (a third-person view) rather
    /// than noise. Cam swap (C key / wheel button) still works.
    /// </summary>
    public class SimCameraRig : MonoBehaviour
    {
        public Camera fpvCamera;
        public Camera chaseCamera;
        public RenderTexture fpvTex;
        public RenderTexture chaseTex;

        public void Init(Config cfg, Transform carTransform, CockpitBuilder cockpit)
        {
            int w = cfg.video.texWidth;
            int h = cfg.video.texHeight;

            fpvTex = new RenderTexture(w, h, 16, RenderTextureFormat.Default)
            {
                name = "SimFPV",
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear,
            };
            fpvTex.Create();

            chaseTex = new RenderTexture(w, h, 16, RenderTextureFormat.Default)
            {
                name = "SimChase",
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear,
            };
            chaseTex.Create();

            // FPV camera — parented to car, nose-mounted, looking slightly down.
            var fpvGO = new GameObject("SimFPVCam");
            fpvGO.transform.SetParent(carTransform, false);
            fpvGO.transform.localPosition = new Vector3(0, 0.10f, 0.18f);
            fpvGO.transform.localRotation = Quaternion.Euler(4f, 0, 0);
            fpvCamera = fpvGO.AddComponent<Camera>();
            fpvCamera.clearFlags = CameraClearFlags.SolidColor;
            fpvCamera.backgroundColor = new Color(0.55f, 0.65f, 0.75f); // "sky"
            fpvCamera.fieldOfView = 78f;
            fpvCamera.nearClipPlane = 0.03f;
            fpvCamera.farClipPlane = 500f;
            fpvCamera.targetTexture = fpvTex;
            fpvCamera.allowMSAA = true;

            // Chase camera — follows behind and above the car with a soft spring.
            var chaseGO = new GameObject("SimChaseCam");
            chaseGO.transform.position = carTransform.position + new Vector3(0, 2f, -3f);
            chaseCamera = chaseGO.AddComponent<Camera>();
            chaseCamera.clearFlags = CameraClearFlags.SolidColor;
            chaseCamera.backgroundColor = new Color(0.55f, 0.65f, 0.75f);
            chaseCamera.fieldOfView = 60f;
            chaseCamera.nearClipPlane = 0.1f;
            chaseCamera.farClipPlane = 500f;
            chaseCamera.targetTexture = chaseTex;
            chaseGO.AddComponent<ChaseFollower>().target = carTransform;

            // Re-point the cockpit's screens at our render textures.
            cockpit.SetCameraSource(0, fpvTex);
            cockpit.SetCameraSource(1, chaseTex);

            Log.Info("SimCameraRig: FPV + chase cameras live, cockpit re-pointed");
        }

        /// <summary>Drive the chase camera with a spring-damper toward a
        /// behind-and-above offset from the target. Done in LateUpdate so
        /// physics has finished moving the car.</summary>
        private class ChaseFollower : MonoBehaviour
        {
            public Transform target;
            public float height = 1.2f;
            public float distance = 2.8f;
            public float lerpPos = 6f;
            public float lerpRot = 6f;

            private void LateUpdate()
            {
                if (target == null) return;
                Vector3 desiredPos = target.position
                                     - target.forward * distance
                                     + Vector3.up * height;
                transform.position = Vector3.Lerp(transform.position, desiredPos,
                                                   1f - Mathf.Exp(-lerpPos * Time.deltaTime));
                Quaternion desiredRot = Quaternion.LookRotation(
                    (target.position + target.forward * 1.0f + Vector3.up * 0.3f) - transform.position,
                    Vector3.up);
                transform.rotation = Quaternion.Slerp(transform.rotation, desiredRot,
                                                      1f - Mathf.Exp(-lerpRot * Time.deltaTime));
            }
        }
    }
}
