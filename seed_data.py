import sqlite3

def seed_initial_data():
    conn = sqlite3.connect('ro_bot.db')
    c = conn.cursor()
    
    # Tambahkan NPC untuk Summer Race
    npcs = [
        ('Poring', 'Starter', 'Payon Forest', 'East from Payon Town'),
        ('Poporing', 'Poison', 'Ant Hell', 'Level 2, Center Area'),
        ('Angeling', 'Angel', 'Gefenia', 'West Garden'),
        ('Deviling', 'Devil', 'Gefenia', 'East Garden')
    ]
    c.executemany(
        "INSERT INTO npcs (name, theme, map_location, direction) VALUES (?, ?, ?, ?)",
        npcs
    )
    
    # Tambahkan item contoh
    items = [
        ("White Spider Limb", 6325, 8, "Dolomedes", "100%", "dic_fild02", "Wind"),
        ("Elunium", 985, 5, "Myst Case", "0.5%", "ein_dun02", "Neutral"),
        ("Oridecon", 984, 5, "Owl Duke", "0.5%", "lhz_dun03", "Neutral")
    ]
    # Jika membuat tabel items (tambahkan di init_db jika perlu)
    # c.execute('''CREATE TABLE IF NOT EXISTS items (...);''')
    # c.executemany("INSERT INTO items (...) VALUES (?,?,?,?,?,?,?)", items)
    
    conn.commit()
    conn.close()
    print("Data awal berhasil ditambahkan!")

if __name__ == "__main__":
    seed_initial_data()