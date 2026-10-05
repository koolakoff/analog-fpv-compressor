"""Measure usable NVIDIA HEVC encoding, CPU denoise cost and native DVR timing."""

import argparse
import json
from pathlib import Path
import statistics
import subprocess

import recheck_denoise as shared

ROOT = Path(__file__).resolve().parents[1]


def encode_arguments(encoder):
    if encoder == "nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "slow", "-rc", "vbr", "-cq", "28"]
    if encoder == "av1":
        return ["-c:v", "libsvtav1", "-preset", "6", "-crf", "48", "-svtav1-params", "lp=4:film-grain=0:film-grain-denoise=0"]
    return ["-c:v", "libx265", "-preset", "medium", "-crf", "32", "-x265-params", "pools=4:frame-threads=1"]


def validate(path, expected, tools, directory, name):
    actual = shared.frame_times(path, tools)
    error = max((abs(a-b) for a,b in zip(actual,expected)),default=0)
    if len(actual) != len(expected) or error > .0011:
        raise RuntimeError(f"Frame alignment failed: {name}: {len(actual)} vs {len(expected)}, error {error}")
    shared.run(directory,name+"-decode",[tools["ffmpeg"],"-v","error","-xerror","-threads","4","-i",str(path),"-f","null","-"])
    return {"frames":len(actual),"max_timestamp_error_s":error,"decode_passed":True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,required=True)
    parser.add_argument("--clips",type=Path,default=ROOT/"outputs/denoise-recheck-2026-10-05")
    parser.add_argument("--ffmpeg-dir",type=Path)
    args=parser.parse_args()
    directory=args.directory.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    tools=shared.discover_tools(args.ffmpeg_dir)
    cpu_tools=shared.discover_tools()
    gpu=subprocess.run(["nvidia-smi","--query-gpu=name,driver_version","--format=csv,noheader"],capture_output=True,text=True)
    shared.save(directory/"environment.json",{"gpu":gpu.stdout.strip(),"gpu_exit":gpu.returncode,"tools":tools,
        "cpu_tools":cpu_tools,"gpu_ffmpeg_version":subprocess.check_output([tools["ffmpeg"],"-version"],text=True).splitlines()[0],
        "cpu_ffmpeg_version":subprocess.check_output([cpu_tools["ffmpeg"],"-version"],text=True).splitlines()[0]})
    shared.run(directory,"nvenc-runtime-probe",[tools["ffmpeg"],"-v","error","-n","-f","lavfi","-i",
        "testsrc2=size=160x120:rate=10:duration=0.5", *encode_arguments("nvenc"),str(directory/"probe.mkv")])
    results=[]
    for scene,filename,start,duration in shared.SCENES:
        source=args.clips/(scene+"-source.mkv")
        expected=[t-2 for t in shared.frame_times(source,tools) if t>=2]
        for encoder in ("nvenc","av1","x265"):
            for repeat in range(2 if encoder=="nvenc" else 1):
                for mode in (("off","medium") if repeat==0 else ("medium","off")):
                    name=f"{scene}-{encoder}-{mode}-r{repeat+1}"
                    output=directory/(name+".mkv")
                    filters=(["hqdn3d=3:5:1.5:6"] if mode=="medium" else [])+[
                        "trim=start=2","setpts=PTS-2/TB","scale=in_range=full:out_range=limited","format=yuv420p"]
                    binary=cpu_tools["ffmpeg"] if encoder=="av1" else tools["ffmpeg"]
                    command=[binary,"-v","error","-nostdin","-n","-threads","4","-i",str(source),"-an",
                        "-filter_threads","4","-vf",",".join(filters),"-fps_mode","passthrough","-enc_time_base:v","filter",
                        "-color_range","tv",*encode_arguments(encoder),str(output)]
                    run=shared.run(directory,name,command)
                    check=validate(output,expected,tools,directory,name)
                    row={"scene":scene,"encoder":encoder,"mode":mode,"repeat":repeat+1,"seconds":run["wall_seconds"],
                         "bytes":output.stat().st_size,"path":str(output),**check}
                    results.append(row)
                    shared.save(directory/"runs.json",results)
                    print(f"{name}: {row['seconds']:.3f}s, {row['bytes']} bytes",flush=True)
    source=ROOT/"examples/Frantisek.AVI"
    expected=shared.frame_times(source,tools)
    # The unmodified friend command is recorded separately: it does not ask
    # FFmpeg to preserve variable source timestamps or set output color range.
    friend=directory/"frantisek-friend.mkv"
    friend_run=shared.run(directory,"frantisek-friend",[tools["ffmpeg"],"-v","warning","-nostdin","-n","-hwaccel","cuda",
        "-i",str(source),*encode_arguments("nvenc"),"-an",str(friend)])
    friend_times=shared.frame_times(friend,tools)
    friend_row={"scene":"full-frantisek-friend","encoder":"nvenc","mode":"off","repeat":1,
        "seconds":friend_run["wall_seconds"],"bytes":friend.stat().st_size,"path":str(friend),
        "frames":len(friend_times),"source_frames":len(expected),
        "max_timestamp_error_s":max((abs(a-b) for a,b in zip(expected,friend_times)),default=0)}
    results.append(friend_row)
    shared.save(directory/"runs.json",results)
    print(json.dumps(friend_row),flush=True)
    for encoder,mode,repeat in (("nvenc","off",1),("nvenc","medium",1),
                                ("nvenc","medium",2),("nvenc","off",2),("av1","medium",1)):
        name=f"full-frantisek-{encoder}-{mode}-r{repeat}"
        output=directory/(name+".mkv")
        filters=(["hqdn3d=3:5:1.5:6"] if mode=="medium" else [])+[
            "scale=in_range=full:out_range=limited","format=yuv420p"]
        binary=cpu_tools["ffmpeg"] if encoder=="av1" else tools["ffmpeg"]
        command=[binary,"-v","error","-nostdin","-n","-threads","4","-i",str(source),"-an",
            "-filter_threads","4","-vf",",".join(filters),"-fps_mode","passthrough","-enc_time_base:v","filter",
            "-color_range","tv",*encode_arguments(encoder),str(output)]
        run=shared.run(directory,name,command)
        check=validate(output,expected,tools,directory,name)
        row={"scene":"full-frantisek","encoder":encoder,"mode":mode,"repeat":repeat,"seconds":run["wall_seconds"],
            "bytes":output.stat().st_size,"path":str(output),**check}
        results.append(row)
        shared.save(directory/"runs.json",results)
        print(f"{name}: {row['seconds']:.3f}s, {row['bytes']} bytes",flush=True)
    summary=[]
    for scene,encoder,mode in sorted({(r["scene"],r["encoder"],r["mode"]) for r in results}):
        rows=[r for r in results if (r["scene"],r["encoder"],r["mode"])==(scene,encoder,mode)]
        summary.append({"scene":scene,"encoder":encoder,"mode":mode,"seconds":statistics.mean(r["seconds"] for r in rows),
                        "bytes":statistics.mean(r["bytes"] for r in rows),"repetitions":len(rows)})
    shared.save(directory/"summary.json",summary)
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__":
    main()
