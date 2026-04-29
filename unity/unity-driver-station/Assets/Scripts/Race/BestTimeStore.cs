using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Race
{
    /// <summary>
    /// Simple JSON-backed best-times store, one entry per driver. Persisted at
    /// <c>Application.persistentDataPath/besttimes.json</c>. Good enough for
    /// a pilot with a handful of drivers; a real operational deploy would post
    /// to a backend and query it for the venue-wide ladder.
    /// </summary>
    [Serializable]
    public class BestTimeEntry
    {
        public string driver;
        public float lapSeconds;
        public long unixEpoch;
    }

    [Serializable]
    public class BestTimeStoreData
    {
        public List<BestTimeEntry> entries = new List<BestTimeEntry>();
    }

    public class BestTimeStore
    {
        private BestTimeStoreData _data = new BestTimeStoreData();
        private string _path;

        public void Load()
        {
            _path = Path.Combine(Application.persistentDataPath, "besttimes.json");
            try
            {
                if (File.Exists(_path))
                {
                    string json = File.ReadAllText(_path);
                    _data = JsonUtility.FromJson<BestTimeStoreData>(json)
                            ?? new BestTimeStoreData();
                    Log.Info($"BestTimeStore loaded {_data.entries.Count} entries");
                }
                else
                {
                    // Seed with a couple of sample drivers so the leaderboard
                    // doesn't look empty on first run.
                    _data.entries.Add(new BestTimeEntry
                    {
                        driver = "Track Record",
                        lapSeconds = 42.88f,
                        unixEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    });
                    Save();
                }
            }
            catch (Exception e)
            {
                Log.Err($"BestTimeStore load: {e.Message}");
            }
        }

        public void Save()
        {
            try
            {
                string json = JsonUtility.ToJson(_data, prettyPrint: true);
                File.WriteAllText(_path, json);
            }
            catch (Exception e)
            {
                Log.Err($"BestTimeStore save: {e.Message}");
            }
        }

        public float BestFor(string driver)
        {
            foreach (var e in _data.entries)
                if (e.driver == driver) return e.lapSeconds;
            return -1f;
        }

        public void Submit(string driver, float lap)
        {
            for (int i = 0; i < _data.entries.Count; i++)
            {
                if (_data.entries[i].driver == driver)
                {
                    if (lap < _data.entries[i].lapSeconds)
                    {
                        _data.entries[i].lapSeconds = lap;
                        _data.entries[i].unixEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                        Save();
                    }
                    return;
                }
            }
            _data.entries.Add(new BestTimeEntry
            {
                driver = driver,
                lapSeconds = lap,
                unixEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            });
            Save();
        }

        public List<BestTimeEntry> AllSorted()
        {
            var copy = new List<BestTimeEntry>(_data.entries);
            copy.Sort((a, b) => a.lapSeconds.CompareTo(b.lapSeconds));
            return copy;
        }
    }
}
