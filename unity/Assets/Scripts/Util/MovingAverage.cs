namespace RcPilot.Core
{
    /// <summary>
    /// Fixed-size ring-buffer average. Used to smooth telemetry-age, RSSI, etc.
    /// before they hit the HUD — otherwise the HUD flickers on every packet.
    /// </summary>
    public class MovingAverage
    {
        private readonly float[] buf;
        private int idx;
        private int count;
        private float sum;

        public MovingAverage(int window)
        {
            buf = new float[window < 1 ? 1 : window];
        }

        public void Push(float v)
        {
            if (count == buf.Length) sum -= buf[idx];
            buf[idx] = v;
            sum += v;
            idx = (idx + 1) % buf.Length;
            if (count < buf.Length) count++;
        }

        public float Value => count == 0 ? 0f : sum / count;
        public int Count => count;
        public void Clear() { idx = 0; count = 0; sum = 0f; }
    }
}
