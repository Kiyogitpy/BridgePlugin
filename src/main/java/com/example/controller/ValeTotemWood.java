package com.example.controller;

/** Which log routines/vale_totems.py builds and decorates totems with. */
public enum ValeTotemWood
{
	OAK("Oak"),
	WILLOW("Willow"),
	MAPLE("Maple"),
	YEW("Yew"),
	MAGIC("Magic");

	private final String label;

	ValeTotemWood(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
