import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import com.brastasauce.turaelskipping.models.NpcLocation;
import com.brastasauce.turaelskipping.models.SlayerTask;
import net.runelite.api.coords.WorldArea;
import net.runelite.api.coords.WorldPoint;

/** Read-only dump of the Turael Skipping plugin's own task registry (its data, not a reconstruction). */
public class Dump
{
	public static void main(String[] args) throws Exception
	{
		Class<?> reg = Class.forName("com.brastasauce.turaelskipping.SlayerTaskRegistry");
		Field f = reg.getDeclaredField("TURAEL_TASKS");
		f.setAccessible(true);
		@SuppressWarnings("unchecked")
		Map<String, SlayerTask> tasks = (Map<String, SlayerTask>) f.get(null);
		for (Map.Entry<String, SlayerTask> e : tasks.entrySet())
		{
			SlayerTask t = e.getValue();
			System.out.println("## key=" + e.getKey() + " | task=" + t.getName());
			System.out.println("npcIds: " + t.getNpcIds());
			System.out.println("info: " + t.getInformation());
			System.out.println("shortestPath: " + pt(t.getShortestPathWorldPoint()));
			List<WorldPoint> wm = t.getWorldMapLocations();
			StringBuilder sb = new StringBuilder();
			if (wm != null)
			{
				for (WorldPoint p : wm)
				{
					sb.append(pt(p)).append(' ');
				}
			}
			System.out.println("worldMap: " + sb);
			if (t.getLocations() != null)
			{
				for (NpcLocation l : t.getLocations())
				{
					StringBuilder areas = new StringBuilder();
					if (l.getWorldAreas() != null)
					{
						for (WorldArea a : l.getWorldAreas())
						{
							areas.append("[x=").append(a.getX()).append(",y=").append(a.getY())
								.append(",w=").append(a.getWidth()).append(",h=").append(a.getHeight())
								.append(",p=").append(a.getPlane()).append("] ");
						}
					}
					System.out.println("  loc: " + l.getName() + " | teleports=" + Arrays.toString(l.getTeleports())
						+ " | areas=" + areas);
				}
			}
			System.out.println();
		}
	}

	private static String pt(WorldPoint p)
	{
		return p == null ? "null" : p.getX() + "," + p.getY() + "," + p.getPlane();
	}
}
